"""Hosted Garmin authentication and upload adapter.

Garmin credentials and MFA codes exist only during calls into this module. The
only persistable output is the Garmin client's token JSON, which callers must
encrypt before storage.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import uuid4

from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError


class GarminError(RuntimeError):
    def __init__(self, code: str, detail: str | None = None):
        super().__init__(detail or code)
        self.code = code
        self.detail = detail


@dataclass
class Connected:
    token_bundle: str
    display_name: str | None


@dataclass
class MfaRequired:
    attempt_id: str
    expires_in: int


@dataclass
class Published:
    token_bundle: str
    workout_id: str
    schedule_id: str | None


class GarminSession(Protocol):
    def publish(self, workout: Any, date: str, existing: dict | None) -> Published: ...


class Gateway(Protocol):
    def start_login(self, user_id: str, email: str, password: str) -> Connected | MfaRequired: ...

    def complete_mfa(self, user_id: str, attempt_id: str, code: str) -> Connected: ...

    def open_session(self, token_bundle: str) -> GarminSession: ...


@dataclass
class _Attempt:
    user_id: str
    client: Garmin
    expires_at: float


class LiveGarminGateway:
    """Adapter around the unofficial garminconnect client."""

    def __init__(self, attempt_ttl: int = 300):
        self.attempt_ttl = attempt_ttl
        self._attempts: dict[str, _Attempt] = {}
        self._lock = threading.RLock()

    def start_login(self, user_id: str, email: str, password: str) -> Connected | MfaRequired:
        self._expire_attempts()
        client = Garmin(email=email, password=password, return_on_mfa=True)
        try:
            status, _ = client.login()
        except Exception as exc:
            self._clear_credentials(client)
            raise GarminError("garmin_login_failed", _safe_detail(exc)) from exc

        self._clear_credentials(client)
        if status == "needs_mfa":
            attempt_id = f"gla_{uuid4().hex}"
            with self._lock:
                self._drop_user_attempts(user_id)
                self._attempts[attempt_id] = _Attempt(
                    user_id=user_id,
                    client=client,
                    expires_at=time.monotonic() + self.attempt_ttl,
                )
            return MfaRequired(attempt_id=attempt_id, expires_in=self.attempt_ttl)
        return self._connected(client)

    def complete_mfa(self, user_id: str, attempt_id: str, code: str) -> Connected:
        self._expire_attempts()
        with self._lock:
            attempt = self._attempts.pop(attempt_id, None)
        if not attempt or attempt.user_id != user_id:
            raise GarminError("garmin_attempt_expired")
        try:
            attempt.client.resume_login({}, code)
            return self._connected(attempt.client)
        except Exception as exc:
            raise GarminError("garmin_mfa_failed", _safe_detail(exc)) from exc
        finally:
            self._clear_credentials(attempt.client)

    def open_session(self, token_bundle: str) -> "LiveGarminSession":
        """Restore tokens once so a whole plan shares one Garmin login."""
        client = Garmin()
        try:
            # Let the pinned client validate and proactively refresh restored
            # tokens before making write requests.
            client.login(tokenstore=token_bundle)
        except Exception as exc:
            raise _publish_error(exc) from exc
        return LiveGarminSession(client)

    def _connected(self, client: Garmin) -> Connected:
        if not client.client.is_authenticated:
            raise GarminError("garmin_login_failed")
        return Connected(
            token_bundle=client.client.dumps(),
            display_name=client.display_name,
        )

    def _expire_attempts(self) -> None:
        cutoff = time.monotonic()
        with self._lock:
            expired = [key for key, value in self._attempts.items() if value.expires_at <= cutoff]
            for key in expired:
                attempt = self._attempts.pop(key)
                self._clear_credentials(attempt.client)

    def _drop_user_attempts(self, user_id: str) -> None:
        old = [key for key, value in self._attempts.items() if value.user_id == user_id]
        for key in old:
            attempt = self._attempts.pop(key)
            self._clear_credentials(attempt.client)

    @staticmethod
    def _clear_credentials(client: Garmin) -> None:
        client.username = None
        client.password = None


class LiveGarminSession:
    def __init__(self, client: Garmin):
        self._client = client

    def publish(self, workout: Any, date: str, existing: dict | None) -> Published:
        client = self._client
        try:
            if existing:
                workout_id = existing["garmin_workout_id"]
                url = f"{client.garmin_workouts}/workout/{workout_id}"
                payload = workout.to_dict()
                payload["workoutId"] = int(workout_id)
                client.client.put("connectapi", url, json=payload, api=True)
                schedule_id = existing.get("garmin_schedule_id")
                if existing.get("scheduled_date") != date:
                    if schedule_id:
                        try:
                            client.unschedule_workout(int(schedule_id))
                        except Exception as exc:
                            # A schedule the user already removed in Garmin
                            # must not block rescheduling; anything else
                            # would leave a duplicate calendar entry.
                            if not _looks_like_not_found(exc):
                                raise
                    scheduled = client.schedule_workout(int(workout_id), date)
                    schedule_id = scheduled.get("scheduleId") or scheduled.get("workoutScheduleId")
            else:
                uploaded = client.upload_running_workout(workout)
                workout_id = uploaded["workoutId"]
                scheduled = client.schedule_workout(workout_id, date)
                schedule_id = scheduled.get("scheduleId") or scheduled.get("workoutScheduleId")
            return Published(
                token_bundle=client.client.dumps(),
                workout_id=str(workout_id),
                schedule_id=str(schedule_id) if schedule_id is not None else None,
            )
        except GarminError:
            raise
        except Exception as exc:
            raise _publish_error(exc) from exc


class MockGarminGateway:
    """Deterministic development gateway; never makes a Garmin request."""

    def __init__(self):
        self._attempts: dict[str, str] = {}

    def start_login(self, user_id: str, email: str, password: str) -> Connected | MfaRequired:
        if password == "reject-login":
            raise GarminError("garmin_login_failed")
        if email.lower().startswith("mfa+"):
            attempt = f"mock_{uuid4().hex}"
            self._attempts[attempt] = user_id
            return MfaRequired(attempt, 300)
        return Connected(f'{{"mock_user":"{user_id}"}}', email.split("@")[0])

    def complete_mfa(self, user_id: str, attempt_id: str, code: str) -> Connected:
        owner = self._attempts.pop(attempt_id, None)
        if owner != user_id:
            raise GarminError("garmin_attempt_expired")
        if code != "123456":
            raise GarminError("garmin_mfa_failed")
        return Connected(f'{{"mock_user":"{user_id}"}}', "Mock Runner")

    def open_session(self, token_bundle: str) -> "MockGarminSession":
        return MockGarminSession(token_bundle)


class MockGarminSession:
    def __init__(self, token_bundle: str):
        self._token_bundle = token_bundle

    def publish(self, workout: Any, date: str, existing: dict | None) -> Published:
        workout_id = existing["garmin_workout_id"] if existing else f"mock-{uuid4().hex}"
        schedule_id = existing.get("garmin_schedule_id") if existing else f"schedule-{uuid4().hex}"
        return Published(self._token_bundle, workout_id, schedule_id)


def _publish_error(exc: Exception) -> GarminError:
    code = "garmin_reauthentication_required" if _looks_like_auth(exc) else "garmin_upload_failed"
    return GarminError(code, _safe_detail(exc))


def _looks_like_auth(exc: Exception) -> bool:
    # Restoring expired tokens raises GarminConnectAuthenticationError with a
    # message ("Username and password are required") that names no status code.
    if isinstance(exc, GarminConnectAuthenticationError):
        return True
    text = str(exc).lower()
    return any(value in text for value in ("401", "403", "unauthorized", "authentication"))


def _looks_like_not_found(exc: Exception) -> bool:
    return "404" in str(exc)


def _safe_detail(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return text[:300]
