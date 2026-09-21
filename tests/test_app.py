import copy
import asyncio
from datetime import timedelta

import httpx
import pytest
from sqlalchemy import select

from workout_relay.app import create_app
from workout_relay.garmin import MockGarminGateway
from workout_relay.database import BrowserSession, GarminConnection, PlanSubmission, User, now
from workout_relay.plans import EXAMPLE_PLAN
from workout_relay.security import hash_password


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
            changed["workouts"][0]["date"] = "2026-10-09"
            third = await client.post("/api/v1/plans", headers=mutation, json=changed)
            third_result = await wait_for_submission(client, third.json()["id"])
            assert third_result["result"]["counts"] == {
                "created": 0,
                "updated": 1,
                "skipped": 1,
            }
            assert third_result["result"]["workouts"][0]["garmin_workout_id"] == first_result["result"]["workouts"][0]["garmin_workout_id"]

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
            queued = await client.post("/api/v1/plans", headers=bearer, json=EXAMPLE_PLAN)
            assert queued.status_code == 202
            uploaded = await wait_for_submission(client, queued.json()["id"], bearer)
            assert uploaded["status"] == "completed"
            assert uploaded["result"]["counts"]["created"] == 2
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
            assert english["submit_url"] == "http://test/api/v1/plans"
            assert english["status_url_template"].endswith("/plans/{submission_id}")
            assert (await client.get("/readyz")).json() == {"status": "ready"}
            assert (await client.get("/api/v1/me")).headers["cache-control"] == "no-store"
            openapi = (await client.get("/openapi.json")).json()
            assert "HTTPBearer" in openapi["components"]["securitySchemes"]
            assert "WorkoutPlan" in openapi["components"]["schemas"]
            assert openapi["servers"] == [{"url": "http://test"}]
            assert (
                openapi["paths"]["/api/v1/plans"]["post"]["requestBody"]["content"]
                ["application/json"]["schema"]["$ref"]
                == "#/components/schemas/WorkoutPlan"
            )


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


@pytest.mark.anyio
async def test_startup_purges_expired_sessions_and_old_completed_plans(settings):
    app = create_app(settings)
    database = app.state.database
    database.initialize()
    with database.session() as db:
        user = User(
            email="retention@example.com",
            password_hash=hash_password("a-long-test-password"),
        )
        db.add(user)
        db.flush()
        old = now() - timedelta(days=2)
        db.add(
            BrowserSession(
                token_hash="expired-session",
                csrf_hash="expired-csrf",
                user_id=user.id,
                created_at=old,
                expires_at=old,
            )
        )
        db.add(
            PlanSubmission(
                user_id=user.id,
                plan_id="old-plan",
                title="Old plan",
                content="{}",
                status="completed",
                created_at=old,
                completed_at=old,
            )
        )
        db.commit()

    async with app.router.lifespan_context(app):
        with database.session() as db:
            assert db.scalar(select(BrowserSession)) is None
            assert db.scalar(select(PlanSubmission)) is None


class CountingGateway(MockGarminGateway):
    def __init__(self, fail_first_session=False):
        super().__init__()
        self.sessions_opened = 0
        self.fail_first_session = fail_first_session

    def open_session(self, token_bundle):
        self.sessions_opened += 1
        if self.fail_first_session and self.sessions_opened == 1:
            raise RuntimeError("unexpected client failure")
        return super().open_session(token_bundle)


async def connect_and_register(client):
    csrf = await register(client)
    mutation = {"X-CSRF-Token": csrf}
    await client.post(
        "/api/v1/garmin/connect/start",
        headers=mutation,
        json={"email": "garmin@example.com", "password": "garmin-password"},
    )
    return mutation


@pytest.mark.anyio
async def test_one_garmin_login_per_plan(settings):
    gateway = CountingGateway()
    app = create_app(settings, gateway)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            mutation = await connect_and_register(client)
            queued = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            result = await wait_for_submission(client, queued.json()["id"])
            assert result["result"]["counts"]["created"] == 2
            assert gateway.sessions_opened == 1

            # A fully unchanged plan needs no Garmin login at all.
            queued = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            await wait_for_submission(client, queued.json()["id"])
            assert gateway.sessions_opened == 1


@pytest.mark.anyio
async def test_worker_survives_failures(settings, monkeypatch):
    from workout_relay import app as app_module

    real = app_module.process_next_submission
    calls = {"count": 0}

    async def flaky(*args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("database blip")
        return await real(*args)

    monkeypatch.setattr(app_module, "process_next_submission", flaky)
    app = create_app(settings, CountingGateway(fail_first_session=True))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            mutation = await connect_and_register(client)
            first = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            first_result = await wait_for_submission(client, first.json()["id"])
            assert first_result["status"] == "failed"
            assert first_result["result"]["code"] == "internal_upload_error"

            second = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            second_result = await wait_for_submission(client, second.json()["id"])
            assert second_result["status"] == "completed"
