import copy
import time

import pytest

from workout_relay import garmin


def publish(session, workout, date, existing=None, progress=None):
    state = progress if progress is not None else {"stage": "ready", "marker": "[Workout Relay:test]"}

    def checkpoint(value, _tokens):
        state.clear()
        state.update(copy.deepcopy(value))

    return session.publish(workout, date, existing, progress=state, checkpoint=checkpoint)


class FakeInner:
    def __init__(self):
        self.is_authenticated = False

    def dumps(self):
        return '{"token":"secret"}'


class FakeGarmin:
    last = None

    def __init__(self, email=None, password=None, return_on_mfa=False):
        self.username = email
        self.password = password
        self.return_on_mfa = return_on_mfa
        self.client = FakeInner()
        self.display_name = "Runner"
        FakeGarmin.last = self

    def login(self):
        return "needs_mfa", None

    def resume_login(self, state, code):
        assert code == "123456"
        self.client.is_authenticated = True


def test_live_gateway_clears_credentials_while_waiting_for_mfa(monkeypatch):
    monkeypatch.setattr(garmin, "Garmin", FakeGarmin)
    gateway = garmin.LiveGarminGateway()
    result = gateway.start_login("user-1", "runner@example.com", "garmin-password")
    assert isinstance(result, garmin.MfaRequired)
    assert FakeGarmin.last.username is None
    assert FakeGarmin.last.password is None

    connected = gateway.complete_mfa("user-1", result.attempt_id, "123456")
    assert connected.token_bundle == '{"token":"secret"}'
    assert FakeGarmin.last.username is None
    assert FakeGarmin.last.password is None


class FakePublishInner:
    is_authenticated = True

    def dumps(self):
        return '{"token":"refreshed"}'


class FakePublishGarmin:
    last = None
    garmin_workouts = "/workout-service"

    def __init__(self, **_kwargs):
        self.client = FakePublishInner()
        self.restored = None
        FakePublishGarmin.last = self

    def login(self, tokenstore=None):
        self.restored = tokenstore

    def upload_workout(self, _workout):
        return {"workoutId": 42}

    def schedule_workout(self, workout_id, date):
        assert workout_id == 42
        assert date == "2026-10-01"
        return {"scheduleId": 84}


def test_publish_validates_restored_tokens_and_returns_refresh(monkeypatch):
    monkeypatch.setattr(garmin, "Garmin", FakePublishGarmin)
    gateway = garmin.LiveGarminGateway()
    session = gateway.open_session('{"token":"stored"}')
    published = publish(session, FakeWorkout(), "2026-10-01")
    assert FakePublishGarmin.last.restored == '{"token":"stored"}'
    assert published.token_bundle == '{"token":"refreshed"}'
    assert published.workout_id == "42"
    assert published.schedule_id == "84"


class FakeWorkout:
    def __init__(self, name="Moved", description=None):
        self.name = name
        self.description = description

    def to_dict(self):
        payload = {"workoutName": self.name}
        if self.description is not None:
            payload["description"] = self.description
        return payload


class FakeRescheduleInner(FakePublishInner):
    def __init__(self):
        self.puts = []

    def put(self, _domain, url, **kwargs):
        self.puts.append((url, kwargs["json"]))
        return {}


class FakeRescheduleGarmin(FakePublishGarmin):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.client = FakeRescheduleInner()
        self.unscheduled = []
        self.scheduled = []

    def unschedule_workout(self, schedule_id):
        self.unscheduled.append(schedule_id)

    def schedule_workout(self, workout_id, date):
        self.scheduled.append((workout_id, date))
        return {"workoutScheduleId": 99}


def test_moving_a_workout_removes_the_old_calendar_entry(monkeypatch):
    monkeypatch.setattr(garmin, "Garmin", FakeRescheduleGarmin)
    session = garmin.LiveGarminGateway().open_session('{"token":"stored"}')
    published = publish(session,
        FakeWorkout(),
        "2026-10-03",
        {"garmin_workout_id": "42", "garmin_schedule_id": "84", "scheduled_date": "2026-10-01"},
    )
    client = FakeRescheduleGarmin.last
    assert client.client.puts == [
        ("/workout-service/workout/42", {"workoutName": "Moved", "workoutId": 42})
    ]
    assert client.unscheduled == [84]
    assert client.scheduled == [(42, "2026-10-03")]
    assert published.schedule_id == "99"


class FakeExpiredGarmin(FakePublishGarmin):
    def login(self, tokenstore=None):
        # What garminconnect raises when stored tokens no longer load.
        raise garmin.GarminConnectAuthenticationError("Username and password are required")


def test_expired_tokens_require_reauthentication(monkeypatch):
    monkeypatch.setattr(garmin, "Garmin", FakeExpiredGarmin)
    try:
        garmin.LiveGarminGateway().open_session('{"token":"stored"}')
    except garmin.GarminError as exc:
        assert exc.code == "garmin_reauthentication_required"
    else:
        raise AssertionError("expected GarminError")


class RecoveryGarmin(FakeRescheduleGarmin):
    def __init__(self, fail_at=None, accepted=True, list_descriptions=True, sortable=True):
        super().__init__()
        self.fail_at = fail_at
        self.accepted = accepted
        self.list_descriptions = list_descriptions
        self.sortable = sortable
        self.creates = 0
        self.schedules = 0
        self.workouts = []
        self.calendar = []
        self.listings = []
        self.details = []

    def _listed(self, items):
        if self.list_descriptions:
            return [dict(item) for item in items]
        return [{k: v for k, v in item.items() if k != "description"} for item in items]

    def connectapi(self, url, params=None):
        assert url.endswith("/workouts")
        if not self.sortable:
            raise garmin.GarminConnectConnectionError("API Error 400 - unknown parameter")
        assert (params["orderBy"], params["orderSeq"]) == ("createdDate", "DESC")
        self.listings.append(params)
        newest_first = list(reversed(self.workouts))
        return self._listed(newest_first[params["start"]:params["start"] + params["limit"]])

    def get_workout_by_id(self, workout_id):
        self.details.append(workout_id)
        return next(item for item in self.workouts if item["workoutId"] == workout_id)

    def upload_workout(self, payload):
        self.creates += 1
        if self.fail_at != "create" or self.accepted:
            self.workouts.append(dict(payload, workoutId=42))
        if self.fail_at == "create":
            self.fail_at = None
            raise TimeoutError("response lost")
        return {"workoutId": 42}

    def schedule_workout(self, workout_id, date):
        self.schedules += 1
        if self.fail_at != "schedule" or self.accepted:
            self.calendar.append({"id": 84, "workoutId": workout_id, "date": date, "itemType": "workout"})
        if self.fail_at == "schedule":
            self.fail_at = None
            raise TimeoutError("response lost")
        return {"scheduleId": 84}

    def get_workouts(self, start=0, limit=100):
        self.listings.append({"start": start, "limit": limit})
        return self._listed(list(reversed(self.workouts))[start:start + limit])

    def get_scheduled_workouts(self, year, month):
        assert (year, month) == (2026, 10)
        return {"calendarItems": self.calendar}


@pytest.mark.parametrize("fail_at", ["create", "schedule"])
def test_timeout_after_remote_acceptance_reconciles_without_duplicate_posts(fail_at):
    client = RecoveryGarmin(fail_at)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    if fail_at == "schedule":
        assert state["workout_id"] == "42"  # creation survived the failed schedule
    result = publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert (result.workout_id, result.schedule_id) == ("42", "84")
    assert (client.creates, client.schedules) == (1, 1)
    assert state["stage"] == "completed"


@pytest.mark.parametrize("fail_at", ["create", "schedule"])
def test_unconfirmed_remote_outcome_never_blindly_repeats_post(fail_at):
    client = RecoveryGarmin(fail_at, accepted=False)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    before = (client.creates, client.schedules)
    for _ in range(2):
        with pytest.raises(garmin.GarminError) as error:
            publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
        assert error.value.code == "garmin_outcome_unknown"
    assert (client.creates, client.schedules) == before


def test_checkpoint_failure_prevents_the_next_remote_write():
    client = RecoveryGarmin()
    saved = []

    def checkpoint(state, tokens):
        saved.append(copy.deepcopy(state))
        if state["stage"] == "created":
            raise RuntimeError("database unavailable")

    with pytest.raises(garmin.GarminError):
        garmin.LiveGarminSession(client).publish(FakeWorkout(), "2026-10-01", None,
            progress={"stage": "ready", "marker": "[Workout Relay:test]"}, checkpoint=checkpoint)
    assert client.creates == 1 and client.schedules == 0
    state = saved[0]  # last successful durable checkpoint
    publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert client.creates == 1 and client.schedules == 1


def test_rejected_schedule_retries_without_creating_another_workout():
    client = RecoveryGarmin()
    original = client.schedule_workout
    attempts = []

    def rejected_once(workout_id, date):
        attempts.append(workout_id)
        if len(attempts) == 1:
            raise garmin.GarminConnectConnectionError("API Error 429 - rate limited")
        return original(workout_id, date)

    client.schedule_workout = rejected_once
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert state["stage"] == "created" and state["workout_id"] == "42"
    publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert attempts == [42, 42]
    assert client.creates == client.schedules == 1


@pytest.mark.parametrize("error", [TimeoutError("429"), garmin.GarminConnectConnectionError("API Error 500 - 429")])
def test_only_confirmed_client_rejections_authorize_post_retry(error):
    assert not garmin._definitely_rejected(error)


def filler(client, count, name="Moved", newer=True):
    """Other workouts sharing the name but carrying no recovery marker.

    Listings are newest first, so appended entries are scanned before anything
    created earlier.
    """
    for index in range(count):
        entry = {"workoutId": 1000 + index, "workoutName": name, "description": "unrelated"}
        client.workouts.append(entry) if newer else client.workouts.insert(0, entry)


def test_reconciliation_stays_within_its_request_budget():
    client = RecoveryGarmin("create", list_descriptions=False)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    # The created workout is now buried behind newer ones, beyond the budget.
    filler(client, 60)
    with pytest.raises(garmin.GarminError) as error:
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert error.value.code == "garmin_outcome_unknown"
    assert len(client.listings) == garmin.RECONCILE_PAGES
    assert len(client.details) == garmin.RECONCILE_DETAIL_REQUESTS
    assert client.creates == 1  # exhaustion never authorizes another POST


def test_reconciliation_waits_out_its_cooldown_before_spending_requests():
    client = RecoveryGarmin("create", accepted=False)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert state["reconcile_after"] > time.time()
    spent = len(client.listings)
    with pytest.raises(garmin.GarminError) as error:
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert error.value.code == "garmin_outcome_unknown"
    assert len(client.listings) == spent  # cooldown spends nothing

    state["reconcile_after"] = time.time() - 1
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert len(client.listings) > spent


def test_reconciliation_finds_the_workout_without_listed_descriptions():
    client = RecoveryGarmin("create", list_descriptions=False)
    filler(client, 3)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    result = publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert result.workout_id == "42"
    # Newest first, so the just-created workout is the first detail fetched.
    assert client.creates == 1 and client.details[0] == 42


def test_listing_falls_back_when_sort_parameters_are_rejected():
    client = RecoveryGarmin("create", sortable=False)
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    result = publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert result.workout_id == "42"
    assert client.listings and all("orderBy" not in listing for listing in client.listings)


def test_rate_limited_listing_stops_reconciliation():
    client = RecoveryGarmin("create")

    def rate_limited(*_args, **_kwargs):
        raise garmin.GarminConnectConnectionError("API Error 429 - slow down")

    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    with pytest.raises(garmin.GarminError):
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    client.connectapi = rate_limited
    with pytest.raises(garmin.GarminError) as error:
        publish(garmin.LiveGarminSession(client), FakeWorkout(), "2026-10-01", progress=state)
    assert error.value.code == "garmin_outcome_unknown"
    assert client.creates == 1


def test_created_workout_carries_a_marker_that_cleanup_removes():
    client = RecoveryGarmin()
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    workout = FakeWorkout(description="Easy run")
    published = publish(garmin.LiveGarminSession(client), workout, "2026-10-01", progress=state)
    assert client.workouts[0]["description"] == "Easy run\n[Workout Relay:test]"
    assert state["cleanup"] == "pending"

    session = garmin.LiveGarminSession(client)
    saved = {}
    session.cleanup_marker(workout, published.workout_id, progress=state,
                           checkpoint=lambda value, _tokens: saved.update(value))
    url, payload = client.client.puts[-1]
    assert url.endswith("/workout/42") and payload["description"] == "Easy run"
    assert saved["cleanup"] is None and client.creates == 1


def test_long_description_is_truncated_around_an_intact_marker():
    marker = "[Workout Relay:test]"
    described = garmin._with_marker("x" * garmin.DESCRIPTION_LIMIT, marker)
    assert len(described) == garmin.DESCRIPTION_LIMIT and described.endswith(marker)


def test_moving_back_to_a_date_whose_calendar_entry_was_deleted():
    client = RecoveryGarmin()
    state = {"stage": "ready", "marker": "[Workout Relay:test]"}
    workout = FakeWorkout()
    publish(garmin.LiveGarminSession(client), workout, "2026-10-01", progress=state)
    client.calendar.clear()  # the user deleted the entry in Garmin Connect

    def missing(schedule_id):
        raise garmin.GarminConnectConnectionError("API Error 404 - not found")

    client.unschedule_workout = missing
    moved = publish(garmin.LiveGarminSession(client), workout, "2026-10-03",
                    existing={"garmin_workout_id": "42", "garmin_schedule_id": "84", "scheduled_date": "2026-10-01"},
                    progress=dict(state, stage="ready"))
    assert moved.workout_id == "42" and client.creates == 1
    assert client.schedules == 2 and state["cleanup"] == "pending"
