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
