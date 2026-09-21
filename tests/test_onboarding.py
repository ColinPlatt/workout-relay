"""Guidance shown until each thing is set up, and clearing plan history."""

import copy
import json

import httpx
import pytest
from dataclasses import replace
from sqlalchemy import select

from workout_relay.app import create_app
from workout_relay.database import PlanSubmission, WorkoutLink
from workout_relay.plans import EXAMPLE_PLAN
from tests.test_connector import call_tool, granted_token, register_account


@pytest.fixture
def onboarding_settings(settings):
    return replace(settings, base_url="http://localhost:8000")


async def client_for(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    )


async def connect_garmin(client, csrf):
    return await client.post(
        "/api/v1/garmin/connect/start",
        headers={"X-CSRF-Token": csrf},
        json={
            "email": "garmin@example.com",
            "password": "garmin-password",
            "retention": "persistent",
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
async def test_setup_guidance_is_present_and_hidden_once_connected(onboarding_settings):
    app = create_app(onboarding_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            page = (await client.get("/")).text
            script = (await client.get("/static/app.js")).text

            # The Garmin walkthrough ships hidden and is revealed by status.
            assert 'id="garmin-setup"' in page and 'class="card setup-card hidden"' in page
            assert '$("#garmin-setup").classList.toggle("hidden", status.connected);' in script
            # It says plainly that nothing is copied from Garmin Connect.
            for key in ("setupIntro", "setupOneBody", "setupTwoBody", "setupFourBody"):
                assert script.count(key) == 2, key  # English and French
                assert f'data-i18n="{key}"' in page
            assert "Apple or Google" in script and "Apple ou Google" in script

            # Diagrams are drawn inline, with titles that translate.
            assert page.count('class="setup-art"') >= 7
            assert 'data-i18n="setupOneTitle"' in page


@pytest.mark.anyio
async def test_assistant_guides_track_which_platform_is_connected(onboarding_settings):
    app = create_app(onboarding_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            page = (await client.get("/")).text
            assert 'id="assistant-setup"' in page
            assert 'id="claude-badge"' in page and 'id="chatgpt-badge"' in page
            assert 'id="claude-guide"' in page and 'id="chatgpt-guide"' in page

            csrf = await register_account(client)
            assert (await client.get("/api/v1/connections")).json()["items"] == []

            await granted_token(client, approve_as_csrf=csrf)
            items = (await client.get("/api/v1/connections")).json()["items"]
            assert len(items) == 1 and items[0]["client_name"] == "Test Assistant"


def test_platform_detection_reads_the_registered_name():
    """A hint from a self-reported name, matched case-insensitively."""
    script = (
        __import__("pathlib").Path("workout_relay/static/app.js").read_text()
    )
    assert 'text.includes("claude") || text.includes("anthropic")' in script
    assert 'text.includes("chatgpt") || text.includes("openai")' in script


@pytest.mark.anyio
async def test_clearing_history_keeps_what_prevents_duplicates(onboarding_settings):
    app = create_app(onboarding_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            mutation = {"X-CSRF-Token": csrf}

            queued = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            await wait_for(client, queued.json()["id"])
            assert len((await client.get("/api/v1/plans")).json()["items"]) == 1

            cleared = await client.delete("/api/v1/plans", headers=mutation)
            assert cleared.status_code == 200
            assert cleared.json() == {"removed": 1, "in_progress": 0}
            assert (await client.get("/api/v1/plans")).json()["items"] == []

            with app.state.database.session() as db:
                # The links survive: they are what stops a re-send duplicating.
                assert len(db.scalars(select(WorkoutLink)).all()) == 2

            # Re-sending the same plan updates nothing and creates nothing.
            again = await client.post("/api/v1/plans", headers=mutation, json=EXAMPLE_PLAN)
            result = await wait_for(client, again.json()["id"])
            assert result["result"]["counts"] == {"created": 0, "updated": 0, "skipped": 2}


@pytest.mark.anyio
async def test_clearing_history_is_per_account_and_needs_the_browser(onboarding_settings):
    app = create_app(onboarding_settings)
    async with app.router.lifespan_context(app):
        async with await client_for(app) as first:
            csrf = await register_account(first, "first@example.com")
            await connect_garmin(first, csrf)
            queued = await first.post(
                "/api/v1/plans", headers={"X-CSRF-Token": csrf}, json=EXAMPLE_PLAN
            )
            await wait_for(first, queued.json()["id"])

            # A CSRF-less call from the same browser is refused.
            assert (await first.delete("/api/v1/plans")).status_code == 403

        async with await client_for(app) as second:
            other_csrf = await register_account(second, "second@example.com")
            _, tokens = await granted_token(second, approve_as_csrf=other_csrf)
            # An assistant token cannot clear anyone's history.
            refused = await second.delete(
                "/api/v1/plans", headers={"Authorization": f"Bearer {tokens['access_token']}"}
            )
            assert refused.status_code == 403
            cleared = await second.delete("/api/v1/plans", headers={"X-CSRF-Token": other_csrf})
            assert cleared.json()["removed"] == 0

        with app.state.database.session() as db:
            # The first account's history is untouched by the second's clear.
            assert len(db.scalars(select(PlanSubmission)).all()) == 1
