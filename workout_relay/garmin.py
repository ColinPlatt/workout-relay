"""Hosted Garmin authentication and upload adapter.

Garmin credentials and MFA codes exist only during calls into this module. The
only persistable output is the Garmin client's token JSON, which callers must
encrypt before storage.
"""

from __future__ import annotations

import threading
import time
import re
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from uuid import uuid4

from garminconnect import Garmin
from garminconnect.exceptions import GarminConnectAuthenticationError, GarminConnectConnectionError


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
    def publish(self, workout: Any, date: str, existing: dict | None, *, progress: dict, checkpoint: Callable) -> Published: ...

    def cleanup_marker(self, workout: Any, workout_id: str, *, progress: dict, checkpoint: Callable) -> None: ...


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


RECONCILE_COOLDOWN_SECONDS = 60
RECONCILE_PAGE_SIZE = 25
RECONCILE_PAGES = 2
RECONCILE_DETAIL_REQUESTS = 8
RECONCILE_REQUEST_BUDGET = RECONCILE_PAGES + RECONCILE_DETAIL_REQUESTS
# Unverified against Garmin; the plan schema already caps descriptions at 500.
DESCRIPTION_LIMIT = 1024


class _RequestBudget:
    """Hard ceiling on reconciliation traffic for one attempt."""

    def __init__(self, limit: int):
        self.remaining = limit

    def spend(self) -> None:
        if self.remaining <= 0:
            raise GarminError("garmin_outcome_unknown")
        self.remaining -= 1


class LiveGarminSession:
    """One restored Garmin login, shared by every workout in a plan.

    Lifetime invariant: every method runs in the upload worker's thread while
    the event loop awaits that thread and upload ownership is still held. No
    method may be called concurrently from two threads.
    """

    def __init__(self, client: Garmin):
        self._client = client
        self._sorted_listing = True

    def publish(
        self,
        workout: Any,
        date: str,
        existing: dict | None,
        *,
        progress: dict,
        checkpoint: Callable,
    ) -> Published:
        """Journal intent before every write; never repeat an ambiguous POST.

        A lost response may mean Garmin accepted the request, so recovery stays
        read-only until the outcome is identified. PUT and DELETE repeat safely.
        Remote identity (workout, calendar entry, scheduled date) is journalled
        independently of the payload, so a changed payload can resume an
        interrupted update without re-creating anything.
        """
        client = self._client
        state = dict(progress)
        payload = workout.to_dict()

        def save(stage: str | None = None, **values):
            if stage is not None:
                values["stage"] = stage
            state.update(values)
            checkpoint(dict(state), client.client.dumps())

        def post(function, retry_stage):
            try:
                return function()
            except Exception as exc:
                if _definitely_rejected(exc):
                    # A confirmed rejection did not apply the write. Preserve an
                    # already-created workout when only scheduling was rejected.
                    save(retry_stage)
                    raise _publish_error(exc) from exc
                raise GarminError("garmin_outcome_unknown") from exc

        try:
            if state.get("stage") == "creating":
                found = self._reconcile_created(payload.get("workoutName"), state, save)
                save("created", workout_id=found, schedule_id=None, scheduled_date=None)

            known = state.get("workout_id") or (existing or {}).get("garmin_workout_id")
            if state.get("stage", "ready") == "ready":
                if known:
                    self._update(payload, known, state, save, existing or {})
                else:
                    self._create(payload, state, save, post)

            if state["stage"] in ("updated", "unscheduling"):
                if state.get("scheduled_date") == date and state.get("schedule_id"):
                    save("completed")
                else:
                    if state.get("schedule_id"):
                        save("unscheduling")
                        try:
                            client.unschedule_workout(int(state["schedule_id"]))
                        except Exception as exc:
                            # DELETE repeats safely, and a precise 404 proves the
                            # calendar entry is already gone.
                            if _api_status(exc) != 404:
                                raise
                    save("created", schedule_id=None, scheduled_date=None)

            if state["stage"] == "scheduling":
                found = self._reconcile_schedule(state["workout_id"], date, state, save)
                save("completed", schedule_id=found, scheduled_date=date)

            if state["stage"] == "created":
                save("scheduling")
                scheduled = post(
                    lambda: client.schedule_workout(int(state["workout_id"]), date), "created"
                )
                identifier = scheduled.get("scheduleId") or scheduled.get("workoutScheduleId")
                save("completed", schedule_id=_remote_id(identifier), scheduled_date=date)

            return Published(
                token_bundle=client.client.dumps(),
                workout_id=state["workout_id"],
                schedule_id=state.get("schedule_id"),
            )
        except GarminError:
            raise
        except Exception as exc:
            raise _publish_error(exc) from exc

    def cleanup_marker(
        self, workout: Any, workout_id: str, *, progress: dict, checkpoint: Callable
    ) -> None:
        """Replace the created workout with the marker-free payload.

        The same repeatable PUT the update path uses, so a failure here is never
        worse than leaving the marker in place for the next submission.
        """
        client = self._client
        payload = dict(workout.to_dict(), workoutId=int(workout_id))
        url = f"{client.garmin_workouts}/workout/{workout_id}"
        client.client.put("connectapi", url, json=payload, api=True)
        checkpoint(dict(progress, cleanup=None), client.client.dumps())

    def _create(self, payload: dict, state: dict, save: Callable, post: Callable) -> None:
        marker = state.get("marker")
        if not marker:
            raise GarminError("garmin_outcome_unknown")
        described = dict(payload)
        described["description"] = _with_marker(payload.get("description") or "", marker)
        save("creating", cleanup="pending")
        uploaded = post(lambda: self._client.upload_workout(described), "ready")
        save(
            "created",
            workout_id=_remote_id(uploaded.get("workoutId")),
            schedule_id=None,
            scheduled_date=None,
        )

    def _update(
        self, payload: dict, workout_id: str, state: dict, save: Callable, existing: dict
    ) -> None:
        url = f"{self._client.garmin_workouts}/workout/{workout_id}"
        save(
            "updating",
            workout_id=str(workout_id),
            schedule_id=state["schedule_id"]
            if "schedule_id" in state
            else existing.get("garmin_schedule_id"),
            scheduled_date=state["scheduled_date"]
            if "scheduled_date" in state
            else existing.get("scheduled_date"),
        )
        self._client.client.put(
            "connectapi", url, json=dict(payload, workoutId=int(workout_id)), api=True
        )
        # This payload carries no marker, so any earlier marker is now gone.
        save("updated", cleanup=None)

    def _reconcile_created(self, workout_name, state: dict, save: Callable) -> str:
        """Identify an ambiguous creation within a fixed request budget."""
        marker = state.get("marker")
        if not marker:
            raise GarminError("garmin_outcome_unknown")
        self._begin_reconciliation(state, save)
        budget = _RequestBudget(RECONCILE_REQUEST_BUDGET)
        matches: list = []
        candidates: list = []
        for page in range(RECONCILE_PAGES):
            items = self._list_workouts(page * RECONCILE_PAGE_SIZE, budget)
            for item in items:
                description = item.get("description")
                if description and marker in description:
                    matches.append(item.get("workoutId"))
                elif not description and item.get("workoutName") == workout_name:
                    # The name only narrows the search; the marker identifies it.
                    candidates.append(item.get("workoutId"))
            if len(items) < RECONCILE_PAGE_SIZE:
                break
        for workout_id in candidates[:RECONCILE_DETAIL_REQUESTS]:
            if budget.remaining <= 0:
                break
            item = self._request(budget, self._client.get_workout_by_id, workout_id)
            if marker in (item.get("description") or ""):
                matches.append(workout_id)
        if len(matches) != 1:
            # Exhaustion or ambiguity stays unknown; it never authorizes a POST.
            raise GarminError("garmin_outcome_unknown")
        return _remote_id(matches[0])

    def _reconcile_schedule(self, workout_id: str, date: str, state: dict, save: Callable) -> str:
        self._begin_reconciliation(state, save)
        year, month, _ = map(int, date.split("-"))
        calendar = self._request(
            _RequestBudget(1), self._client.get_scheduled_workouts, year, month
        )
        matches = [
            item.get("id") or item.get("scheduleId")
            for item in calendar.get("calendarItems", [])
            if str(item.get("workoutId")) == str(workout_id)
            and item.get("date") == date
            and item.get("itemType") == "workout"
        ]
        if len(matches) != 1:
            raise GarminError("garmin_outcome_unknown")
        return _remote_id(matches[0])

    def _begin_reconciliation(self, state: dict, save: Callable) -> None:
        """Persist the cooldown before spending any request on a retry."""
        if time.time() < state.get("reconcile_after", 0):
            raise GarminError("garmin_outcome_unknown")
        save(reconcile_after=time.time() + RECONCILE_COOLDOWN_SECONDS)

    def _list_workouts(self, start: int, budget: _RequestBudget) -> list:
        client = self._client
        url = f"{client.garmin_workouts}/workouts"
        if self._sorted_listing and hasattr(client, "connectapi"):
            budget.spend()
            try:
                return client.connectapi(
                    url,
                    params={
                        "start": start,
                        "limit": RECONCILE_PAGE_SIZE,
                        "orderBy": "createdDate",
                        "orderSeq": "DESC",
                    },
                )
            except Exception as exc:
                # Newest-first ordering is unverified. Fall back only when the
                # parameters themselves were rejected, never on a rate limit
                # or a connection failure.
                if _api_status(exc) not in {400, 404, 405, 415, 422}:
                    raise GarminError("garmin_outcome_unknown") from exc
                self._sorted_listing = False
        return self._request(budget, client.get_workouts, start=start, limit=RECONCILE_PAGE_SIZE)

    @staticmethod
    def _request(budget: _RequestBudget, function: Callable, *args, **kwargs):
        budget.spend()
        try:
            return function(*args, **kwargs)
        except GarminError:
            raise
        except Exception as exc:
            # Stop on rate limits and connection failures rather than spending
            # the rest of the budget against an unhappy Garmin.
            raise GarminError("garmin_outcome_unknown") from exc


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

    def publish(self, workout: Any, date: str, existing: dict | None, *, progress: dict, checkpoint: Callable) -> Published:
        workout_id = progress.get("workout_id") or (existing["garmin_workout_id"] if existing else f"mock-{uuid4().hex}")
        scheduled_date = progress.get("scheduled_date") or (existing or {}).get("scheduled_date")
        schedule_id = progress.get("schedule_id") or ((existing or {}).get("garmin_schedule_id") if scheduled_date == date else None)
        checkpoint(
            dict(
                progress,
                stage="completed",
                workout_id=workout_id,
                schedule_id=schedule_id or f"schedule-{uuid4().hex}",
                scheduled_date=date,
                cleanup=None,
            ),
            self._token_bundle,
        )
        return Published(self._token_bundle, workout_id, schedule_id or f"schedule-{uuid4().hex}")

    def cleanup_marker(self, workout: Any, workout_id: str, *, progress: dict, checkpoint: Callable) -> None:
        checkpoint(dict(progress, cleanup=None), self._token_bundle)


def _publish_error(exc: Exception) -> GarminError:
    code = "garmin_reauthentication_required" if _looks_like_auth(exc) else "garmin_upload_failed"
    return GarminError(code, _safe_detail(exc))


def _remote_id(value) -> str:
    if value is None or not str(value).isdigit() or int(value) <= 0:
        raise GarminError("garmin_outcome_unknown")
    return str(value)


def _with_marker(description: str, marker: str) -> str:
    """Keep the marker intact; truncate the user's description if needed."""
    room = DESCRIPTION_LIMIT - len(marker) - 1
    return (description[:room] + "\n" + marker) if description else marker


def _api_status(exc: Exception) -> int | None:
    """Status code of a rejection, or None when the outcome is not a response.

    The pinned 0.3.2 client discards the Response and raises this exact prefix.
    Never infer a status from arbitrary exception text.
    """
    if not isinstance(exc, GarminConnectConnectionError):
        return None
    match = re.match(r"^API Error (\d{3})(?: - |$)", str(exc))
    return int(match[1]) if match else None


def _definitely_rejected(exc: Exception) -> bool:
    if isinstance(exc, GarminConnectAuthenticationError):
        return True
    return _api_status(exc) in {400, 401, 403, 404, 405, 413, 415, 422, 429}


def _looks_like_auth(exc: Exception) -> bool:
    # Restoring expired tokens raises GarminConnectAuthenticationError with a
    # message ("Username and password are required") that names no status code.
    if isinstance(exc, GarminConnectAuthenticationError):
        return True
    text = str(exc).lower()
    return any(value in text for value in ("401", "403", "unauthorized", "authentication"))


def _safe_detail(exc: Exception) -> str:
    text = str(exc).replace("\n", " ")
    return text[:300]
