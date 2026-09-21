"""Where Garmin tokens may live is the person's choice, and it is enforced."""

import copy
import json
from dataclasses import replace
from datetime import timedelta

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy import select

from workout_relay.app import create_app
from workout_relay.database import Database, GarminConnection, now
from workout_relay.plans import EXAMPLE_PLAN


@pytest.fixture
def visit_settings(settings):
    return replace(settings, base_url="http://localhost:8000")


async def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    )


async def register(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "runner@example.com", "password": "a-long-test-password"},
    )
    return response.json()["csrf_token"]


async def connect(client, csrf, retention):
    return await client.post(
        "/api/v1/garmin/connect/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "garmin@example.com",
            "password": "garmin-password",
            "retention": retention,
        },
    )


async def wait_for(client, submission_id):
    import asyncio

    for _ in range(100):
        body = (await client.get(f"/api/v1/plans/{submission_id}")).json()
        if body["status"] in {"completed", "failed"}:
            return body
        await asyncio.sleep(0.01)
    raise AssertionError("submission did not finish")


@pytest.mark.anyio
async def test_visit_only_connection_writes_no_tokens_to_the_database(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            connected = await connect(client, csrf, "visit")
            assert connected.json()["retention"] == "visit"

            with app.state.database.session() as db:
                row = db.scalar(select(GarminConnection))
                assert row.retention == "visit"
                assert row.encrypted_tokens == ""
                assert row.visit_expires_at is not None
                audit = db.execute(
                    sa.text("SELECT detail FROM audit_events WHERE event = 'garmin.connected'")
                ).scalar()
                # The choice is recorded, which is what demonstrates consent.
                assert json.loads(audit)["retention"] == "visit"

            status = (await client.get("/api/v1/garmin/status")).json()
            assert status["connected"] is True and status["retention"] == "visit"
            assert status["visit_expires_at"] is not None


@pytest.mark.anyio
async def test_keeping_connected_stores_tokens_as_before(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            await connect(client, csrf, "persistent")
            with app.state.database.session() as db:
                row = db.scalar(select(GarminConnection))
                assert row.retention == "persistent"
                assert row.encrypted_tokens and row.visit_expires_at is None
            status = (await client.get("/api/v1/garmin/status")).json()
            assert status["retention"] == "persistent" and status["visit_expires_at"] is None


@pytest.mark.anyio
async def test_a_visit_upload_works_until_the_window_closes(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            await connect(client, csrf, "visit")
            mutation = {"X-CSRF-Token": csrf}

            first = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            assert (await wait_for(client, first.json()["id"]))["status"] == "completed"

            # Expire the window server-side, as the clock would.
            store = app.state.tokens
            user_id = store._held and next(iter(store._held))
            ciphertext, _ = store._held[user_id]
            store._held[user_id] = (ciphertext, now() - timedelta(seconds=1))

            status = (await client.get("/api/v1/garmin/status")).json()
            assert status["connected"] is False and status["status"] == "visit_expired"

            changed = copy.deepcopy(EXAMPLE_PLAN)
            changed["workouts"][0]["title"] = "After the window"
            second = await client.post("/api/v1/plans", headers=mutation, json=changed)
            result = await wait_for(client, second.json()["id"])
            assert result["status"] == "failed"
            assert result["result"]["code"] == "garmin_visit_expired"


@pytest.mark.anyio
async def test_refreshing_tokens_does_not_extend_the_window(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            await connect(client, csrf, "visit")
            store = app.state.tokens
            user_id = next(iter(store._held))
            original = store._held[user_id][1]

            await client.post("/api/v1/plans", headers={"X-CSRF-Token": csrf}, json=EXAMPLE_PLAN)
            await wait_for(client, (await client.get("/api/v1/plans")).json()["items"][0]["id"])

            assert store._held[user_id][1] == original


@pytest.mark.anyio
async def test_disconnecting_forgets_tokens_held_in_memory(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            await connect(client, csrf, "visit")
            assert app.state.tokens._held
            removed = await client.delete(
                "/api/v1/garmin/connection", headers={"X-CSRF-Token": csrf}
            )
            assert removed.status_code == 204
            assert app.state.tokens._held == {}


@pytest.mark.anyio
async def test_the_choice_cannot_be_made_by_accident(visit_settings):
    """Omitting the choice is refused rather than defaulted either way."""
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register(client)
            response = await client.post(
                "/api/v1/garmin/connect/start",
                headers={"X-CSRF-Token": csrf},
                json={"email": "garmin@example.com", "password": "garmin-password"},
            )
            assert response.status_code == 422
            bad = await client.post(
                "/api/v1/garmin/connect/start",
                headers={"X-CSRF-Token": csrf},
                json={
                    "email": "garmin@example.com",
                    "password": "garmin-password",
                    "retention": "forever",
                },
            )
            assert bad.status_code == 422


def test_existing_databases_gain_the_columns_without_losing_rows(tmp_path):
    """A release that adds columns must not need a manual migration."""
    url = f"sqlite:///{tmp_path / 'old.db'}"
    engine = sa.create_engine(url)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE garmin_connections (user_id VARCHAR(36) PRIMARY KEY, "
            "encrypted_tokens TEXT, display_name VARCHAR(200), status VARCHAR(30), "
            "last_validated_at DATETIME, created_at DATETIME, updated_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO garmin_connections VALUES ('u1','cipher','Runner','connected',null,null,null)"
        )
    engine.dispose()

    database = Database(url)
    database.initialize()
    database.initialize()  # idempotent
    with database.session() as db:
        row = db.get(GarminConnection, "u1")
        assert row.encrypted_tokens == "cipher"
        # Existing connections keep the arrangement they were made under.
        assert row.retention == "persistent"
        assert row.visit_expires_at is None
    database.engine.dispose()


@pytest.mark.anyio
async def test_assistants_are_told_when_the_window_has_closed(visit_settings):
    """An assistant must not be told "connected" when it cannot deliver."""
    from tests.test_connector import call_tool, granted_token, register_account

    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register_account(client)
            await connect(client, csrf, "visit")
            _, tokens = await granted_token(client, approve_as_csrf=csrf)

            body = json.loads(
                (await call_tool(client, tokens["access_token"], "get_garmin_status"))
                .json()["result"]["content"][0]["text"]
            )
            assert body["connected"] is True and body["retention"] == "visit"
            assert "one visit only" in body["note"]

            store = app.state.tokens
            user_id = next(iter(store._held))
            ciphertext, _ = store._held[user_id]
            store._held[user_id] = (ciphertext, now() - timedelta(seconds=1))

            closed = json.loads(
                (await call_tool(client, tokens["access_token"], "get_garmin_status"))
                .json()["result"]["content"][0]["text"]
            )
            assert closed["connected"] is False and closed["status"] == "visit_expired"


@pytest.mark.anyio
async def test_the_interface_offers_the_choice_and_explains_storage(visit_settings):
    app = create_app(visit_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            page = (await client.get("/")).text
            script = (await client.get("/static/app.js")).text
            assert 'name="retention" value="visit"' in page
            # "Keep connected" is pre-selected; the API still requires the
            # choice to be stated, so nothing is decided by omission.
            assert 'name="retention" value="persistent" checked' in page
            assert 'value="visit" checked' not in page
            assert 'id="privacy-dialog"' in page and 'id="privacy-note"' in page
            for key in ("retentionVisitHelp", "privacyCookies", "error_garmin_visit_expired"):
                assert key in script
            # Both languages carry the new copy.
            assert script.count("retentionVisitHelp") >= 2
