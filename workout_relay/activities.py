"""Bounded, account-scoped activity reads shared by REST and MCP.

Only explicit metrics are returned. Raw Garmin payloads can include GPS and
account details and must never be returned or logged. Garmin can also serve
public activities belonging to someone else, so detail IDs must first appear
in this account's own listing.
"""

from __future__ import annotations

import math
import re
from datetime import timedelta

from sqlalchemy import update

from .database import ActivityAccess, GarminConnection, now, _as_utc
from .garmin import GarminError
from .rate_limit import RateLimiter


class ActivityError(RuntimeError):
    def __init__(self, code: str, status: int = 502):
        super().__init__(code)
        self.code, self.status = code, status


def activity_json(data: dict) -> dict:
    summary = data.get("summaryDTO", data)
    if not isinstance(summary, dict):
        raise ActivityError("garmin_activity_response_invalid")
    activity_id = str(data.get("activityId", ""))
    if not re.fullmatch(r"[1-9][0-9]{0,19}", activity_id):
        raise ActivityError("garmin_activity_response_invalid")
    kind = data.get("activityTypeDTO", data.get("activityType", {}))

    def text(value, maximum=200):
        return value[:maximum] if isinstance(value, str) else None

    result = {
        "id": activity_id, "name": text(data.get("activityName")),
        "sport": text(kind.get("typeKey")) if isinstance(kind, dict) else None,
        "start_time_local": text(summary.get("startTimeLocal"), 40),
        "start_time_gmt": text(summary.get("startTimeGMT"), 40),
    }
    fields = {
        "distance_m": "distance", "duration_s": "duration",
        "moving_duration_s": "movingDuration", "elapsed_duration_s": "elapsedDuration",
        "average_speed_m_s": "averageSpeed", "max_speed_m_s": "maxSpeed",
        "average_heart_rate_bpm": "averageHR", "max_heart_rate_bpm": "maxHR",
        "elevation_gain_m": "elevationGain", "elevation_loss_m": "elevationLoss",
        "calories_kcal": "calories", "average_power_w": "avgPower",
        "max_power_w": "maxPower", "aerobic_training_effect": "trainingEffect",
        "anaerobic_training_effect": "anaerobicTrainingEffect",
    }
    for name, source in fields.items():
        value = summary.get(source)
        result[name] = value if type(value) in (int, float) and math.isfinite(value) else None
    speed = result["average_speed_m_s"]
    pace = 1000 / speed if speed and speed > 0 else None
    result["average_pace_s_per_km"] = pace if pace is not None and math.isfinite(pace) else None
    return result


class ActivityService:
    def __init__(self, database, tokens, gateway):
        # `tokens` honours the account's retention choice: a visit-only
        # connection keeps its Garmin tokens in memory, never in the row.
        self.database, self.tokens, self.gateway = database, tokens, gateway
        self.limiter = RateLimiter()

    def list(self, user_id: str, limit: int = 5, start: int = 0, sport: str | None = None):
        if type(limit) is not int or not 1 <= limit <= 50 or type(start) is not int or not 0 <= start <= 100000:
            raise ActivityError("invalid_activity_query", 422)
        if sport is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", sport):
            raise ActivityError("invalid_activity_query", 422)
        return self._read(user_id, None, limit, start, sport)

    def get(self, user_id: str, activity_id: str):
        if not re.fullmatch(r"[1-9][0-9]{0,19}", activity_id):
            raise ActivityError("invalid_activity_id", 422)
        return self._read(user_id, activity_id)

    def _read(self, user_id, activity_id, limit=5, start=0, sport=None):
        if not self.limiter.allow(user_id, 20, 60):
            raise ActivityError("rate_limited", 429)
        try:
            return self._read_owned(user_id, activity_id, limit, start, sport)
        except ActivityError:
            raise
        except Exception:
            # This includes vault/DB/token-export failures outside the Garmin
            # call itself. Never forward their potentially sensitive details.
            raise ActivityError("garmin_activity_read_failed") from None

    def _read_owned(self, user_id, activity_id, limit, start, sport):
        # Use the same cross-process lock as uploads to serialize Garmin token
        # refresh. The lock and all DB operations live in this thread, even if
        # the async caller is cancelled. No background thread releases it early.
        with self.database.upload_owner() as owned:
            if owned is None:
                raise ActivityError("garmin_busy", 503)
            with self.database.session() as db:
                connection = db.get(GarminConnection, user_id)
                if connection is None:
                    raise ActivityError("garmin_not_connected", 409)
                if connection.status != "connected":
                    raise ActivityError("garmin_reauthentication_required", 409)
                original_tokens = connection.encrypted_tokens
                tokens = self.tokens.read(connection)
                db.commit()
                session = None
                result = None
                error = None
                observed = []
                try:
                    session = self.gateway.open_session(tokens)
                    account = session.activity_account()
                    owned()
                    if activity_id is None:
                        raw = session.list_activities(start, limit, sport)
                        if not isinstance(raw, list) or len(raw) > limit or any(not isinstance(item, dict) for item in raw):
                            raise ActivityError("garmin_activity_response_invalid")
                        items = [activity_json(item) for item in raw]
                        observed = items
                        result = {"items": items, "start": start, "limit": limit,
                                  "next_start": start + len(items) if len(items) == limit else None}
                    else:
                        access = db.get(ActivityAccess, (user_id, activity_id))
                        if access is None or access.garmin_account != account or _as_utc(access.observed_at) < now() - timedelta(days=1):
                            raise ActivityError("activity_not_found", 404)
                        raw = session.get_activity(activity_id)
                        if not isinstance(raw, dict):
                            raise ActivityError("garmin_activity_response_invalid")
                        activity = activity_json(raw)
                        if activity["id"] != activity_id:
                            raise ActivityError("garmin_activity_response_invalid")
                        result = {"activity": activity}
                except ActivityError as exc:
                    error = exc
                except GarminError as exc:
                    code = "garmin_activity_read_failed" if exc.code == "garmin_upload_failed" else exc.code
                    status = {"garmin_reauthentication_required": 409, "activity_not_found": 404, "garmin_rate_limited": 429}.get(code, 502)
                    error = ActivityError(code, status)
                except Exception:
                    # Never expose/log an upstream response containing health data.
                    error = ActivityError("garmin_activity_read_failed")
                if error:
                    db.rollback()
                owned()
                values = {}
                if error and error.code == "garmin_reauthentication_required":
                    values["status"] = "reauthentication_required"
                elif session is not None:
                    # Returns None for a visit-only connection, which keeps its
                    # refreshed tokens in memory instead of the row.
                    refreshed = self.tokens.remember(connection, session.activity_tokens())
                    if refreshed is not None:
                        values["encrypted_tokens"] = refreshed
                if values:
                    changed = db.execute(update(GarminConnection).where(
                        GarminConnection.user_id == user_id,
                        GarminConnection.encrypted_tokens == original_tokens,
                        GarminConnection.status == "connected",
                    ).values(**values))
                    if changed.rowcount != 1:
                        db.rollback()
                        raise ActivityError("garmin_connection_changed", 409)
                if not error:
                    # Commit ownership references only after the conditional
                    # connection update: concurrent disconnect/reconnect must
                    # neither resurrect a connection nor authorize old IDs.
                    for item in observed:
                        db.merge(ActivityAccess(user_id=user_id, activity_id=item["id"], garmin_account=account, observed_at=now()))
                    self.database.audit(db, "activities.read", user_id, {"kind": "detail" if activity_id else "list"})
                db.commit()
                if error:
                    raise error
                return result
