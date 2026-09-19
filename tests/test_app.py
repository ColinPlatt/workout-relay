import copy
import asyncio

import httpx
import pytest
from sqlalchemy import select

from workout_relay.app import create_app
from workout_relay.database import GarminConnection, User
from workout_relay.plans import EXAMPLE_PLAN


async def register(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "runner@example.com", "password": "a-long-test-password"},
    )
    assert response.status_code == 201, response.text
    return response.json()["csrf_token"]


async def wait_for_submission(client, submission_id, headers=None):
    for _ in range(100):
        response = await client.get(
            f"/api/v1/plans/{submission_id}", headers=headers or {}
        )
        assert response.status_code == 200, response.text
        submission = response.json()
        if submission["status"] in {"completed", "failed"}:
            return submission
        await asyncio.sleep(0.01)
    raise AssertionError(f"submission {submission_id} did not finish")


@pytest.mark.anyio
async def test_complete_mobile_flow_and_idempotency(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            csrf = await register(client)
            mutation = {"X-CSRF-Token": csrf}

            status = await client.get("/api/v1/garmin/status")
            assert status.json()["connected"] is False
            connected = await client.post(
                "/api/v1/garmin/connect/start",
                headers=mutation,
                json={"email": "garmin@example.com", "password": "garmin-password"},
            )
            assert connected.json()["status"] == "connected"

            with app.state.database.session() as db:
                user = db.scalar(select(User).where(User.email == "runner@example.com"))
                connection = db.get(GarminConnection, user.id)
                assert "mock_user" not in connection.encrypted_tokens
                assert "garmin-password" not in connection.encrypted_tokens

            validated = await client.post(
                "/api/v1/plans/validate", headers=mutation, json=EXAMPLE_PLAN
            )
            assert validated.json()["workout_count"] == 2

            first = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            assert first.status_code == 202, first.text
            assert first.json()["status"] == "queued"
            first_result = await wait_for_submission(client, first.json()["id"])
            assert first_result["result"]["counts"] == {
                "created": 2,
                "updated": 0,
                "skipped": 0,
            }

            second = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            second_result = await wait_for_submission(client, second.json()["id"])
            assert second_result["result"]["counts"] == {
                "created": 0,
                "updated": 0,
                "skipped": 2,
            }

            changed = copy.deepcopy(EXAMPLE_PLAN)
            changed["workouts"][0]["description"] = "Updated"
            third = await client.post("/api/v1/plans", headers=mutation, json=changed)
            third_result = await wait_for_submission(client, third.json()["id"])
            assert third_result["result"]["counts"] == {
                "created": 0,
                "updated": 1,
                "skipped": 1,
            }

            history = (await client.get("/api/v1/plans")).json()["items"]
            assert [item["status"] for item in history] == ["completed"] * 3


@pytest.mark.anyio
async def test_mfa_api_key_language_and_disconnect(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await register(client)
            mutation = {"X-CSRF-Token": csrf}

            language = await client.put(
                "/api/v1/me/language", headers=mutation, json={"language": "fr"}
            )
            assert language.json()["preferred_language"] == "fr"

            started = await client.post(
                "/api/v1/garmin/connect/start",
                headers=mutation,
                json={"email": "mfa+runner@example.com", "password": "garmin-password"},
            )
            assert started.json()["status"] == "mfa_required"
            completed = await client.post(
                "/api/v1/garmin/connect/complete",
                headers=mutation,
                json={"attempt_id": started.json()["attempt_id"], "code": "123456"},
            )
            assert completed.json()["status"] == "connected"

            created = await client.post(
                "/api/v1/api-keys", headers=mutation, json={"name": "Claude"}
            )
            token = created.json()["token"]
            assert token.startswith("wkr_")
            bearer = {"Authorization": f"Bearer {token}"}
            assert (await client.post("/api/v1/plans/validate", headers=bearer, json=EXAMPLE_PLAN)).status_code == 200
            assert (
                await client.post(
                    "/api/v1/api-keys", headers=bearer, json={"name": "forbidden"}
                )
            ).status_code == 403

            disconnected = await client.delete("/api/v1/garmin/connection", headers=mutation)
            assert disconnected.status_code == 204
            assert (await client.get("/api/v1/garmin/status")).json()["connected"] is False


@pytest.mark.anyio
async def test_structured_validation_and_bilingual_assets(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            root = await client.get("/")
            script = await client.get("/static/app.js")
            assert root.status_code == script.status_code == 200
            assert 'id="language"' in root.text
            assert "Structured workouts" in script.text
            assert "entraînements structurés" in script.text
            assert "workoutRelayLanguage" in script.text

            csrf = await register(client)
            response = await client.post(
                "/api/v1/plans/validate",
                headers={"X-CSRF-Token": csrf},
                json={"schema_version": 9, "workouts": []},
            )
            assert response.status_code == 422
            errors = response.json()["detail"]["errors"]
            assert all(set(item) == {"path", "code", "params"} for item in errors)

            english = (await client.get("/api/v1/plan-format?language=en")).json()
            french = (await client.get("/api/v1/plan-format?language=fr")).json()
            assert english["language"] == "en" and french["language"] == "fr"
            openapi = (await client.get("/openapi.json")).json()
            assert "HTTPBearer" in openapi["components"]["securitySchemes"]


@pytest.mark.anyio
async def test_password_change_and_account_deletion_purge_secrets(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            csrf = await register(client)
            headers = {"X-CSRF-Token": csrf}
            await client.post(
                "/api/v1/garmin/connect/start",
                headers=headers,
                json={"email": "garmin@example.com", "password": "garmin-password"},
            )
            changed = await client.post(
                "/api/v1/me/password",
                headers=headers,
                json={
                    "current_password": "a-long-test-password",
                    "new_password": "a-new-long-test-password",
                },
            )
            assert changed.status_code == 200
            csrf = changed.json()["csrf_token"]

            removed = await client.request(
                "DELETE",
                "/api/v1/account",
                headers={"X-CSRF-Token": csrf},
                json={"password": "a-new-long-test-password"},
            )
            assert removed.status_code == 204
            assert (await client.get("/api/v1/me")).status_code == 401
            with app.state.database.session() as db:
                assert db.scalar(select(User).where(User.email == "runner@example.com")) is None
                assert db.scalar(select(GarminConnection)) is None
