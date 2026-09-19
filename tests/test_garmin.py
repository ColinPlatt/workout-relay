from workout_relay import garmin


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

    def upload_running_workout(self, _workout):
        return {"workoutId": 42}

    def schedule_workout(self, workout_id, date):
        assert workout_id == 42
        assert date == "2026-10-01"
        return {"scheduleId": 84}


def test_publish_validates_restored_tokens_and_returns_refresh(monkeypatch):
    monkeypatch.setattr(garmin, "Garmin", FakePublishGarmin)
    gateway = garmin.LiveGarminGateway()
    session = gateway.open_session('{"token":"stored"}')
    published = session.publish(object(), "2026-10-01", None)
    assert FakePublishGarmin.last.restored == '{"token":"stored"}'
    assert published.token_bundle == '{"token":"refreshed"}'
    assert published.workout_id == "42"
    assert published.schedule_id == "84"


class FakeWorkout:
    def to_dict(self):
        return {"workoutName": "Moved"}


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
    published = session.publish(
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
