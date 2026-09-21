import json
from datetime import timedelta

import pytest
from sqlalchemy import select

from workout_relay.activities import ActivityError, activity_json
from workout_relay.app import CONSENT_TEXT, create_app
from workout_relay.database import ActivityAccess, GarminConnection, now
from workout_relay.garmin import GarminError, LiveGarminSession, MockGarminSession
from workout_relay.security import TokenVault
from test_connector import (
    app_client, call_tool, connect_garmin, connector_settings,
    granted_token, register_account,
)


def payload(response):
    assert response.status_code == 200, response.text
    return json.loads(response.json()["result"]["content"][0]["text"])


def test_metrics_are_allowlisted_and_have_units():
    raw = {"activityId": 123, "activityName": "Run", "activityTypeDTO": {"typeKey": "running"},
           "summaryDTO": {"distance": 5000, "averageSpeed": 2.5, "averageHR": 0,
                          "maxHR": float("nan"), "startLatitude": 42},
           "ownerId": "private", "gps": "private"}
    result = activity_json(raw)
    assert result["distance_m"] == 5000
    assert result["average_pace_s_per_km"] == 400
    assert result["average_heart_rate_bpm"] == 0
    assert result["max_heart_rate_bpm"] is None
    assert result["duration_s"] is None
    assert "private" not in json.dumps(result) and "Latitude" not in json.dumps(result)
    assert activity_json({"activityId": 1, "averageSpeed": 0})["average_pace_s_per_km"] is None
    with pytest.raises(ActivityError):
        activity_json({"activityId": "not-an-id"})


@pytest.mark.anyio
async def test_fresh_activity_only_consent_mcp_and_rest(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            _, tokens = await granted_token(client, csrf, scope="activities:read")
            token = tokens["access_token"]
            listed = payload(await call_tool(client, token, "list_activities", {"limit": 1, "sport": "running"}))
            assert listed["ok"] and listed["next_start"] == 1
            item = listed["items"][0]
            detail = payload(await call_tool(client, token, "get_activity", {"activity_id": item["id"]}))
            assert detail["activity"]["distance_m"] == 5000
            assert detail["activity"]["average_pace_s_per_km"] == 360
            assert payload(await call_tool(client, token, "list_activities", {"start": 1}))["items"] == []
            headers = {"Authorization": f"Bearer {token}"}
            response = await client.get("/api/v1/activities", headers=headers)
            assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
            assert (await client.get(f"/api/v1/activities/{item['id']}", headers=headers)).status_code == 200
            assert (await client.get("/api/v1/plans", headers=headers)).status_code == 403
            assert (await call_tool(client, token, "list_recent_plans")).json()["result"]["isError"]
            assert (await client.get("/api/v1/activities?limit=51", headers=headers)).status_code == 422


@pytest.mark.anyio
async def test_existing_grants_and_keys_cannot_gain_activity_access(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            registration, tokens = await granted_token(client, csrf)
            assert payload(await call_tool(client, tokens["access_token"], "list_activities"))["code"] == "scope_required"
            assert (await client.get("/api/v1/activities", headers={"Authorization": f"Bearer {tokens['access_token']}"})).status_code == 403
            response = await client.post("/token", data={
                "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"],
                "client_id": registration["client_id"], "client_secret": registration["client_secret"],
                "scope": "plans:read plans:write activities:read",
            })
            assert response.status_code == 400
            key = (await client.post("/api/v1/api-keys", json={"name": "old defaults"}, headers={"X-CSRF-Token": csrf})).json()
            assert (await client.get("/api/v1/activities", headers={"Authorization": f"Bearer {key['token']}"})).status_code == 403
            key = (await client.post("/api/v1/api-keys", json={"name": "read only", "scopes": ["activities:read"]}, headers={"X-CSRF-Token": csrf})).json()
            assert (await client.get("/api/v1/activities", headers={"Authorization": f"Bearer {key['token']}"})).status_code == 200


@pytest.mark.anyio
async def test_activity_ids_are_account_bound_expire_and_disconnect_cascades(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as first, await app_client(app) as second:
            csrf = await register_account(first)
            await connect_garmin(first, csrf)
            item = (await first.get("/api/v1/activities")).json()["items"][0]
            csrf2 = await register_account(second, "other@example.com")
            await connect_garmin(second, csrf2)
            assert (await second.get(f"/api/v1/activities/{item['id']}")).status_code == 404
            assert (await first.get("/api/v1/activities/999999")).status_code == 404
            with app.state.database.session() as db:
                access = db.scalar(select(ActivityAccess))
                access.observed_at = now() - timedelta(days=2)
                db.commit()
            assert (await first.get(f"/api/v1/activities/{item['id']}")).status_code == 404
            await first.get("/api/v1/activities")
            with app.state.database.session() as db:
                connection = db.scalar(select(GarminConnection).where(GarminConnection.user_id == access.user_id))
                db.delete(connection)
                db.commit()
                assert db.scalar(select(ActivityAccess)) is None


@pytest.mark.anyio
async def test_busy_and_rate_limits(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            assert (await client.get("/api/v1/activities")).status_code == 409
            await connect_garmin(client, csrf)
            with app.state.database.upload_owner() as owned:
                assert owned is not None
                response = await client.get("/api/v1/activities")
                assert response.status_code == 503
            for _ in range(20):
                response = await client.get("/api/v1/activities")
            assert response.status_code == 429


def test_activity_consent_is_explicit_in_both_languages():
    assert "heart rate" in CONSENT_TEXT["en"]["activities:read"]
    assert "fréquence cardiaque" in CONSENT_TEXT["fr"]["activities:read"]


@pytest.mark.anyio
@pytest.mark.parametrize("fail", [False, True])
async def test_refreshed_tokens_survive_read_success_or_failure(connector_settings, monkeypatch, fail):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            monkeypatch.setattr(MockGarminSession, "activity_tokens", lambda self: "refreshed-secret")
            if fail:
                def failed(*args):
                    raise RuntimeError("private health data")
                monkeypatch.setattr(MockGarminSession, "list_activities", failed)
            response = await client.get("/api/v1/activities")
            assert response.status_code == (502 if fail else 200)
            assert "private health data" not in response.text
            with app.state.database.session() as db:
                connection = db.scalar(select(GarminConnection))
                assert TokenVault(connector_settings.master_encryption_key).decrypt(connection.encrypted_tokens) == "refreshed-secret"


@pytest.mark.anyio
async def test_reconnected_account_cannot_reuse_observed_ids(connector_settings, monkeypatch):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            activity = (await client.get("/api/v1/activities")).json()["items"][0]
            monkeypatch.setattr(MockGarminSession, "activity_account", lambda self: "different-account")
            def unexpected(*args):
                pytest.fail("Must refuse before asking Garmin for detail")
            monkeypatch.setattr(MockGarminSession, "get_activity", unexpected)
            assert (await client.get(f"/api/v1/activities/{activity['id']}")).status_code == 404


def test_live_adapter_uses_bounded_client_methods_and_sanitizes_errors():
    from garminconnect import GarminConnectConnectionError

    class Client:
        def get_activities(self, **kwargs):
            assert kwargs == {"start": 25, "limit": 5, "activitytype": "running"}
            return []

        def get_activity(self, activity_id):
            assert activity_id == "123"
            raise GarminConnectConnectionError("API Error 429 - private response")

    session = LiveGarminSession(Client())
    assert session.list_activities(25, 5, "running") == []
    with pytest.raises(GarminError) as exc:
        session.get_activity("123")
    assert exc.value.code == "garmin_rate_limited"
    assert "private" not in str(exc.value)
