import base64
import copy
import hashlib
import json
import re
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from sqlalchemy import select

from workout_relay.app import create_app
from workout_relay.database import GarminConnection, OAuthToken, PlanSubmission, User
from workout_relay.plans import EXAMPLE_PLAN

REDIRECT = "https://assistant.example/callback"


@pytest.fixture
def connector_settings(settings):
    """OAuth needs an HTTPS issuer; localhost is the exception it allows."""
    return replace(settings, base_url="http://localhost:8000")


def verifier_pair():
    verifier = "a" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def register_account(client, email="runner@example.com"):
    response = await client.post(
        "/api/v1/auth/register", json={"email": email, "password": "a-long-test-password"}
    )
    assert response.status_code == 201, response.text
    return response.json()["csrf_token"]


async def connect_garmin(client, csrf):
    await client.post(
        "/api/v1/garmin/connect/start",
        headers={"X-CSRF-Token": csrf},
        json={"email": "garmin@example.com", "password": "garmin-password", "retention": "persistent"},
    )


async def register_client(client, name="Test Assistant"):
    response = await client.post(
        "/register",
        json={
            "client_name": name,
            "redirect_uris": [REDIRECT],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
        },
    )
    assert response.status_code in (200, 201), response.text
    return response.json()


async def authorize(client, registration, challenge, scope="plans:read plans:write"):
    """Follow the authorize redirect to the consent page and return its id."""
    response = await client.get(
        "/authorize",
        params={
            "client_id": registration["client_id"],
            "redirect_uri": REDIRECT,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
            "scope": scope,
        },
    )
    assert response.status_code in (302, 307), response.text
    location = response.headers["location"]
    assert "/oauth/consent?request=" in location
    return parse_qs(urlsplit(location).query)["request"][0]


async def exchange(client, registration, code, verifier):
    return await client.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT,
            "client_id": registration["client_id"],
            "client_secret": registration.get("client_secret", ""),
            "code_verifier": verifier,
        },
    )


async def call_tool(client, token, name, arguments=None):
    """One stateless MCP tool call, returning the tool's JSON payload."""
    response = await client.post(
        "/mcp/",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
    )
    return response


async def granted_token(client, approve_as_csrf=None, scope="plans:read plans:write"):
    """Complete the whole flow and return the token response."""
    registration = await register_client(client)
    verifier, challenge = verifier_pair()
    grant_id = await authorize(client, registration, challenge, scope=scope)
    consent = await client.post(
        "/oauth/consent",
        data={"request": grant_id, "action": "approve", "csrf": approve_as_csrf},
    )
    assert consent.status_code == 303, consent.text
    code = parse_qs(urlsplit(consent.headers["location"]).query)["code"][0]
    tokens = await exchange(client, registration, code, verifier)
    assert tokens.status_code == 200, tokens.text
    return registration, tokens.json()


async def app_client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://localhost:8000")


@pytest.mark.anyio
async def test_discovery_documents_live_at_the_site_root(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            metadata = await client.get("/.well-known/oauth-authorization-server")
            assert metadata.status_code == 200, metadata.text
            body = metadata.json()
            assert body["authorization_endpoint"] == "http://localhost:8000/authorize"
            assert body["token_endpoint"] == "http://localhost:8000/token"
            assert body["registration_endpoint"] == "http://localhost:8000/register"
            assert "S256" in body["code_challenge_methods_supported"]

            resource = await client.get("/.well-known/oauth-protected-resource/mcp/")
            assert resource.status_code == 200, resource.text
            assert resource.json()["authorization_servers"] == ["http://localhost:8000/"]


@pytest.mark.anyio
async def test_connector_refuses_calls_without_a_token(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            response = await client.post(
                "/mcp/",
                headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert response.status_code == 401


@pytest.mark.anyio
async def test_authorize_alone_grants_nothing(connector_settings):
    """A parked request must not become a usable code without consent."""
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            registration = await register_client(client)
            _, challenge = verifier_pair()
            grant_id = await authorize(client, registration, challenge)
            with app.state.database.session() as db:
                from workout_relay.database import OAuthGrant

                grant = db.get(OAuthGrant, grant_id)
                assert grant.stage == "pending"
                assert grant.user_id is None and grant.code_hash is None


@pytest.mark.anyio
async def test_consent_requires_sign_in_then_issues_a_code(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            registration = await register_client(client)
            verifier, challenge = verifier_pair()
            grant_id = await authorize(client, registration, challenge)

            page = await client.get("/oauth/consent", params={"request": grant_id})
            assert page.status_code == 200
            assert "Test Assistant" in page.text
            assert "send workout plans" in page.text
            # Signed out: the page asks for credentials, not approval.
            assert "name='password'" in page.text and "value='approve'" not in page.text

            with app.state.database.session() as db:
                db.add(
                    User(
                        email="runner@example.com",
                        password_hash=__import__(
                            "workout_relay.security", fromlist=["hash_password"]
                        ).hash_password("a-long-test-password"),
                    )
                )
                db.commit()

            signed_in = await client.post(
                "/oauth/consent",
                data={
                    "request": grant_id,
                    "action": "login",
                    "email": "runner@example.com",
                    "password": "a-long-test-password",
                },
            )
            assert signed_in.status_code == 303
            assert "/oauth/consent?request=" in signed_in.headers["location"]

            page = await client.get("/oauth/consent", params={"request": grant_id})
            assert "runner@example.com" in page.text and "approve" in page.text

            approved = await client.post(
                "/oauth/consent",
                data={
                    "request": grant_id,
                    "action": "approve",
                    "csrf": client.cookies.get("workout_relay_csrf"),
                },
            )
            assert approved.status_code == 303
            target = urlsplit(approved.headers["location"])
            assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT
            query = parse_qs(target.query)
            assert query["state"] == ["xyz"] and query["code"][0].startswith("wkc_")

            tokens = await exchange(client, registration, query["code"][0], verifier)
            assert tokens.status_code == 200, tokens.text
            assert tokens.json()["token_type"].lower() == "bearer"


@pytest.mark.anyio
async def test_denied_consent_returns_an_error_not_a_code(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            registration = await register_client(client)
            _, challenge = verifier_pair()
            grant_id = await authorize(client, registration, challenge)
            denied = await client.post(
                "/oauth/consent", data={"request": grant_id, "action": "deny"}
            )
            assert denied.status_code == 303
            query = parse_qs(urlsplit(denied.headers["location"]).query)
            assert query["error"] == ["access_denied"] and "code" not in query


@pytest.mark.anyio
async def test_authorization_code_is_single_use(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            registration = await register_client(client)
            verifier, challenge = verifier_pair()
            grant_id = await authorize(client, registration, challenge)
            approved = await client.post(
                "/oauth/consent",
                data={"request": grant_id, "action": "approve", "csrf": csrf},
            )
            code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
            assert (await exchange(client, registration, code, verifier)).status_code == 200
            replayed = await exchange(client, registration, code, verifier)
            assert replayed.status_code == 400


@pytest.mark.anyio
async def test_wrong_pkce_verifier_is_rejected(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            registration = await register_client(client)
            _, challenge = verifier_pair()
            grant_id = await authorize(client, registration, challenge)
            approved = await client.post(
                "/oauth/consent",
                data={"request": grant_id, "action": "approve", "csrf": csrf},
            )
            code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
            response = await exchange(client, registration, code, "b" * 64)
            assert response.status_code == 400


@pytest.mark.anyio
async def test_connector_submits_a_plan_for_the_granting_account(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            _, tokens = await granted_token(client, approve_as_csrf=csrf)
            access = tokens["access_token"]

            listed = await call_tool(client, access, "get_garmin_status")
            payload = json.loads(listed.json()["result"]["content"][0]["text"])
            assert payload["connected"] is True

            valid = await call_tool(client, access, "validate_plan_tool", {"plan": EXAMPLE_PLAN})
            assert json.loads(valid.json()["result"]["content"][0]["text"])["workout_count"] == 2

            broken = copy.deepcopy(EXAMPLE_PLAN)
            broken["workouts"][0]["title"] = ""
            invalid = await call_tool(client, access, "validate_plan_tool", {"plan": broken})
            body = json.loads(invalid.json()["result"]["content"][0]["text"])
            assert body["ok"] is False and body["code"] == "plan_invalid" and body["errors"]

            submitted = await call_tool(client, access, "submit_plan", {"plan": EXAMPLE_PLAN})
            accepted = json.loads(submitted.json()["result"]["content"][0]["text"])
            assert accepted["ok"] is True and accepted["status"] == "queued"

            with app.state.database.session() as db:
                submission = db.get(PlanSubmission, accepted["id"])
                user = db.scalar(select(User).where(User.email == "runner@example.com"))
                assert submission.user_id == user.id


@pytest.mark.anyio
async def test_revoked_connection_stops_working(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            await connect_garmin(client, csrf)
            _, tokens = await granted_token(client, approve_as_csrf=csrf)
            access = tokens["access_token"]

            listed = (await client.get("/api/v1/connections")).json()["items"]
            assert len(listed) == 1 and listed[0]["client_name"] == "Test Assistant"
            assert listed[0]["scopes"] == ["plans:read", "plans:write"]

            removed = await client.delete(
                f"/api/v1/connections/{listed[0]['id']}", headers={"X-CSRF-Token": csrf}
            )
            assert removed.status_code == 204
            refused = await call_tool(client, access, "get_garmin_status")
            assert refused.status_code == 401
            assert (await client.get("/api/v1/connections")).json()["items"] == []


@pytest.mark.anyio
async def test_refresh_rotates_and_keeps_the_connection(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            registration, tokens = await granted_token(client, approve_as_csrf=csrf)
            refreshed = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": registration["client_id"],
                    "client_secret": registration.get("client_secret", ""),
                },
            )
            assert refreshed.status_code == 200, refreshed.text
            rotated = refreshed.json()
            assert rotated["access_token"] != tokens["access_token"]
            assert (await call_tool(client, rotated["access_token"], "get_garmin_status")).status_code == 200
            # The spent refresh token cannot be used again.
            reused = await client.post(
                "/token",
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": registration["client_id"],
                    "client_secret": registration.get("client_secret", ""),
                },
            )
            assert reused.status_code == 400
            # Still one connection, not two.
            assert len((await client.get("/api/v1/connections")).json()["items"]) == 1


@pytest.mark.anyio
async def test_one_account_cannot_reach_another(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client, "first@example.com")
            await connect_garmin(client, csrf)
            _, tokens = await granted_token(client, approve_as_csrf=csrf)
            submitted = await call_tool(client, tokens["access_token"], "submit_plan", {"plan": EXAMPLE_PLAN})
            submission_id = json.loads(submitted.json()["result"]["content"][0]["text"])["id"]

        # A second account, with its own connection, must not see it.
        async with await app_client(app) as other:
            other_csrf = await register_account(other, "second@example.com")
            _, other_tokens = await granted_token(other, approve_as_csrf=other_csrf)
            status = await call_tool(
                other, other_tokens["access_token"], "get_plan_status", {"submission_id": submission_id}
            )
            body = json.loads(status.json()["result"]["content"][0]["text"])
            assert body["ok"] is False and body["code"] == "plan_not_found"


@pytest.mark.anyio
async def test_tokens_are_never_stored_in_readable_form(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            _, tokens = await granted_token(client, approve_as_csrf=csrf)
            with app.state.database.session() as db:
                stored = db.scalars(select(OAuthToken)).all()
                blob = json.dumps([row.token_hash for row in stored])
            assert tokens["access_token"] not in blob
            assert tokens["refresh_token"] not in blob
            assert len(stored) == 2


@pytest.mark.anyio
async def test_connector_absent_when_disabled(settings):
    app = create_app(replace(settings, connector_enabled=False, base_url="http://localhost:8000"))
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            assert (await client.get("/.well-known/oauth-authorization-server")).status_code == 404
            assert (await client.post("/mcp/", json={})).status_code == 404


@pytest.mark.anyio
async def test_ui_and_llm_instructions_both_carry_the_connector_address(connector_settings):
    """Users and assistants must be told the same address and steps."""
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            page = (await client.get("/")).text
            script = (await client.get("/static/app.js")).text
            assert 'id="connector-url"' in page and "connectAssistant" in page
            assert "claudeStep1" in script and "chatgptStep3" in script
            # The key section no longer claims Claude can use a bearer key.
            assert "Claude cannot use a bearer key" in page

            for language, marker in (("en", "Add custom connector"), ("fr", "connecteur personnalisé")):
                info = (await client.get(f"/api/v1/plan-format?language={language}")).json()
                assert info["connector"]["url"] == "http://localhost:8000/mcp/"
                assert any(marker in step for step in info["connector"]["setup"])
                assert "submit_plan" in info["connector"]["preferred"]


@pytest.mark.anyio
async def test_a_client_that_requests_no_scope_still_works(connector_settings):
    """RFC 6749 requires a default when scope is omitted.

    Granting none produced a connection that authorized successfully and then
    failed every call with scope_required, which is what a connector reports
    as "authorized, but the server returned an error".
    """
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            registration = await register_client(client)
            verifier, challenge = verifier_pair()

            # No scope parameter at all.
            response = await client.get(
                "/authorize",
                params={
                    "client_id": registration["client_id"],
                    "redirect_uri": REDIRECT,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "state": "xyz",
                },
            )
            grant_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]

            page = await client.get("/oauth/consent", params={"request": grant_id})
            # The person is told what they are approving, not shown an empty list.
            assert "send workout plans" in page.text

            approved = await client.post(
                "/oauth/consent", data={"request": grant_id, "action": "approve", "csrf": csrf}
            )
            code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
            tokens = await exchange(client, registration, code, verifier)
            assert tokens.status_code == 200
            assert tokens.json()["scope"] == "plans:read plans:write"

            # The whole point: a tool call works.
            result = await call_tool(client, tokens.json()["access_token"], "get_garmin_status")
            body = json.loads(result.json()["result"]["content"][0]["text"])
            assert body["ok"] is True


@pytest.mark.anyio
async def test_health_data_is_never_granted_by_default(connector_settings):
    """activities:read covers health measurements, so it must be asked for."""
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            csrf = await register_account(client)
            registration = await register_client(client)
            verifier, challenge = verifier_pair()
            response = await client.get(
                "/authorize",
                params={
                    "client_id": registration["client_id"],
                    "redirect_uri": REDIRECT,
                    "response_type": "code",
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                },
            )
            grant_id = parse_qs(urlsplit(response.headers["location"]).query)["request"][0]
            approved = await client.post(
                "/oauth/consent", data={"request": grant_id, "action": "approve", "csrf": csrf}
            )
            code = parse_qs(urlsplit(approved.headers["location"]).query)["code"][0]
            tokens = await exchange(client, registration, code, verifier)
            assert "activities:read" not in tokens.json()["scope"]
