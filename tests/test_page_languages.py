from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.requests import Request

from workout_relay.app import create_app, consent_html
from workout_relay.pages import page_language
from test_connector import (app_client, authorize, connector_settings, exchange,
                            register_account, register_client, verifier_pair)


@pytest.mark.parametrize("header,expected", [
    ("en-GB,en;q=0.9,fr;q=0.2", "en"), ("fr-FR,fr;q=0.9,en;q=0.8", "fr"),
    ("en;q=0.2,fr;q=0.9", "fr"), ("fr;q=0,en;q=0.5", "en"),
    ("de,fr;q=0.7", "fr"), ("fr;q=invalid", "en"),
])
def test_language_respects_browser_priorities(header, expected):
    request = Request({"type": "http", "headers": [(b"accept-language", header.encode())], "query_string": b""})
    assert page_language(request) == expected


def test_expired_consent_still_has_accessible_language_controls():
    html = consent_html("fr", expired=True)
    assert 'lang="fr"' in html
    assert "🇫🇷" in html and "🇬🇧" in html
    assert 'aria-label="Langue"' in html
    assert "lang=en" in html


@pytest.mark.anyio
async def test_language_survives_oauth_login_error_redirect_and_approval(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            await register_account(client)
            client.cookies.clear()
            registration = await register_client(client)
            verifier, challenge = verifier_pair()
            grant = await authorize(client, registration, challenge, scope="plans:read activities:read")
            page = await client.get("/oauth/consent", params={"request": grant, "lang": "fr"}, headers={"Accept-Language": "en"})
            assert 'lang="fr"' in page.text and "🇬🇧" in page.text
            assert f"request={grant}&amp;lang=en" in page.text
            assert "fréquence cardiaque" in page.text
            assert page.headers["cache-control"] == "no-store"
            form = {"request": grant, "lang": "fr", "action": "login", "email": "runner@example.com", "password": "wrong"}
            failed = await client.post("/oauth/consent", data=form)
            assert failed.status_code == 401 and 'lang="fr"' in failed.text
            assert "incorrect" in failed.text
            form["password"] = "a-long-test-password"
            login = await client.post("/oauth/consent", data=form)
            assert login.status_code == 303 and "lang=fr" in login.headers["location"]
            page = await client.get(login.headers["location"])
            assert "Autoriser" in page.text
            consent = await client.post("/oauth/consent", data={"request": grant, "lang": "fr", "action": "approve", "csrf": client.cookies["workout_relay_csrf"]})
            code = parse_qs(urlsplit(consent.headers["location"]).query)["code"][0]
            token = await exchange(client, registration, code, verifier)
            assert token.status_code == 200
            assert set(token.json()["scope"].split()) == {"plans:read", "activities:read"}


@pytest.mark.anyio
async def test_about_is_public_bilingual_and_does_not_promise_push_feedback(connector_settings):
    app = create_app(connector_settings)
    async with app.router.lifespan_context(app):
        async with await app_client(app) as client:
            for language in ("en", "fr"):
                page = await client.get(f"/about?lang={language}")
                assert page.status_code == 200 and f'lang="{language}"' in page.text
                assert "🇬🇧" in page.text and "🇫🇷" in page.text
                assert "Claude" in page.text and "ChatGPT" in page.text
                assert page.text.count('class="workflow-number"') == 4
            # Explicit selection persists to the next server-rendered page.
            expired = await client.get("/oauth/consent", headers={"Accept-Language": "en"})
            assert 'lang="fr"' in expired.text
            english = await client.get("/about?lang=en")
            assert "does not start chats or send automatic post-run feedback" in english.text
            assert "compatible Garmin device" in english.text
            assert "This visit only" in english.text
