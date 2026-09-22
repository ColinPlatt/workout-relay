"""Workout Relay HTTP API and mobile web application."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import uuid4

from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from mcp.server.auth.routes import build_metadata, create_auth_routes
from mcp.server.auth.settings import ClientRegistrationOptions, RevocationOptions
from pydantic import AnyHttpUrl
from pydantic import BaseModel, EmailStr, Field, SecretStr
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import Settings
from .pages import LANGUAGE_COOKIE, about_html, language_links, page_language
from .activities import (
    ACTIVITY_ACCESS_TTL,
    ActivityError,
    ActivityService,
    purge_expired_activity_access,
)
from .database import (
    ApiKey,
    BrowserSession,
    Database,
    GarminConnection,
    PlanSubmission,
    User,
    WorkoutLink,
    WorkoutOperation,
    now,
)
from .garmin import Connected, GarminError, Gateway, LiveGarminGateway, MfaRequired, MockGarminGateway
from .mcp_probe import BearerGate, build_server
from .mcp_server import build_connector
from .oauth import SCOPES, RelayOAuthProvider, approve, connections, deny, pending_grant, revoke_family
from .submissions import PlanRejected, check_plan, queue_plan, recent_submissions
from .plans import assistant_instructions, example_plan, plan_schema, validate_plan
from .rate_limit import RateLimiter
from .retention import RETENTIONS, VISIT, GarminTokens
from .security import TokenVault, hash_password, hash_token, opaque_token, verify_password
from .workouts import build_workout, content_hash

logger = logging.getLogger(__name__)

# Free Render instances cannot raise the 30s shutdown window, so stay inside it
# and rely on the journal past the deadline.
SHUTDOWN_DRAIN_SECONDS = 25

# The Garmin client sleeps 3-20s between fetching a sign-in form and posting
# credentials, on each of five strategies it may try, so a legitimate sign-in
# can genuinely take most of a minute. Past this the request answers rather
# than leaving the person watching a spinner.
GARMIN_LOGIN_TIMEOUT_SECONDS = 75

SESSION_COOKIE = "workout_relay_session"
CSRF_COOKIE = "workout_relay_csrf"


class Credentials(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=12, max_length=200)


class LanguagePreference(BaseModel):
    language: str = Field(pattern="^(en|fr)$")


class ApiKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    scopes: list[Literal["plans:read", "plans:write", "activities:read"]] = Field(
        default_factory=lambda: ["plans:read", "plans:write"], min_length=1, max_length=3
    )


class GarminLogin(BaseModel):
    email: EmailStr
    password: SecretStr = Field(min_length=1, max_length=300)
    # No default: where Garmin tokens may live is the person's decision, so
    # the caller has to state it rather than inherit one.
    retention: str = Field(pattern="^(persistent|visit)$")


class GarminMfa(BaseModel):
    attempt_id: str = Field(min_length=10, max_length=100)
    code: SecretStr = Field(min_length=4, max_length=20)
    retention: str = Field(pattern="^(persistent|visit)$")


class PasswordChange(BaseModel):
    current_password: SecretStr = Field(min_length=1, max_length=200)
    new_password: SecretStr = Field(min_length=12, max_length=200)


class AccountDeletion(BaseModel):
    password: SecretStr = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class Actor:
    user: User
    method: str
    scopes: frozenset[str]
    csrf_hash: str | None = None


class UserLockPool:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, user_id: str) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())


def create_app(
    settings: Settings | None = None,
    gateway: Gateway | None = None,
) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.validate()
    database = Database(settings.database_url)
    vault = TokenVault(settings.master_encryption_key)
    tokens_store = GarminTokens(vault, settings.garmin_visit_minutes)
    gateway = gateway or (LiveGarminGateway() if settings.garmin_mode == "live" else MockGarminGateway())
    activities = ActivityService(database, tokens_store, gateway)
    limiter = RateLimiter()
    upload_locks = UserLockPool()
    bearer_scheme = HTTPBearer(auto_error=False)
    queue_event = asyncio.Event()
    worker_stop = asyncio.Event()

    # OAuth requires an HTTPS issuer (localhost excepted), so a development
    # base URL disables the connector rather than failing startup.
    connector_possible = settings.connector_enabled and _issuer_usable(settings.base_url)
    if settings.connector_enabled and not connector_possible:
        logger.warning(
            "assistant connector disabled: BASE_URL %s is neither HTTPS nor localhost",
            settings.base_url,
        )
    connector_provider = RelayOAuthProvider(database, settings.base_url) if connector_possible else None
    connector = (
        build_connector(
            settings, database, queue_event.set, connector_provider, activities, tokens_store
        )
        if connector_possible
        else None
    )
    probe = (
        build_server(
            settings.mcp_probe_samples_dir, settings.base_url, settings.mcp_probe_allowed_hosts
        )
        if settings.mcp_probe_enabled
        else None
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        database.initialize()
        with database.session() as recovery_db:
            recovery_db.execute(
                delete(BrowserSession).where(BrowserSession.expires_at <= now())
            )
            recovery_db.execute(
                delete(PlanSubmission).where(
                    PlanSubmission.status.in_(("completed", "failed")),
                    PlanSubmission.created_at
                    < now() - timedelta(days=settings.plan_retention_days),
                )
            )
            recovery_db.commit()
        purge_expired_activity_access(database)
        worker = asyncio.create_task(
            upload_worker(database, tokens_store, gateway, upload_locks, queue_event, worker_stop)
        )
        activity_cleanup = asyncio.create_task(
            activity_access_cleanup_worker(database, worker_stop)
        )
        try:
            async with AsyncExitStack() as transports:
                if connector is not None:
                    await transports.enter_async_context(connector.session_manager.run())
                if probe is not None:
                    # Synthetic samples only: it reaches neither Garmin nor the
                    # database, and carries no account of its own.
                    logger.warning("MCP file-delivery probe is exposed at /mcp-probe/")
                    await transports.enter_async_context(probe.session_manager.run())
                yield
        finally:
            worker_stop.set()
            queue_event.set()
            # Drain the active operation before releasing database ownership;
            # cancelling to_thread does not stop its Garmin request. The drain
            # is bounded: past the deadline a forced kill is safe, because the
            # journal survives and the advisory lock dies with the connection.
            await drain(worker, SHUTDOWN_DRAIN_SECONDS)
            if not worker.done():
                logger.warning("upload worker still active at the shutdown deadline")
            await drain(activity_cleanup, 5)

    app = FastAPI(
        title="Workout Relay API",
        description="Validate and deliver structured running workouts to Garmin Connect.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.database = database
    app.state.gateway = gateway
    app.state.tokens = tokens_store

    if connector is not None:
        # The SDK's OAuth endpoints belong at the root: a client discovering
        # this server reads /.well-known/… before it ever reaches /mcp/.
        registration_options = ClientRegistrationOptions(
            enabled=True, valid_scopes=list(SCOPES), default_scopes=list(SCOPES)
        )
        revocation_options = RevocationOptions(enabled=True)
        metadata_path = "/.well-known/oauth-authorization-server"

        @app.get(metadata_path, include_in_schema=False)
        async def authorization_server_metadata():
            """The SDK's document, with the issuer spelled as clients expect.

            Pydantic renders a bare origin with a trailing slash, so the issuer
            came out as https://host/ while a client comparing it against the
            origin it was given, https://host, refused to connect. The
            endpoints are unaffected; only the bare origin is normalized.
            """
            document = build_metadata(
                issuer_url=AnyHttpUrl(settings.base_url),
                service_documentation_url=None,
                client_registration_options=registration_options,
                revocation_options=revocation_options,
            ).model_dump(mode="json", exclude_none=True)
            document["issuer"] = settings.base_url
            return document

        app.router.routes.extend(
            route
            for route in create_auth_routes(
                provider=connector_provider,
                issuer_url=AnyHttpUrl(settings.base_url),
                client_registration_options=registration_options,
                revocation_options=revocation_options,
            )
            if getattr(route, "path", "") != metadata_path
        )
        def resource_metadata(resource: str) -> dict:
            """RFC 9728 document naming this exact resource identifier.

            Clients compare the `resource` they read against the URL they were
            given, and they disagree about the trailing slash: one connects to
            /mcp, another to /mcp/. Each path therefore answers with its own
            identifier rather than redirecting, because a redirect changes the
            identifier out from under the comparison. The authorization server
            is named exactly as its own metadata spells it.
            """
            return {
                "resource": resource,
                # Spelled exactly as the issuer above, without a trailing
                # slash, or a client comparing the two rejects the pair.
                "authorization_servers": [settings.base_url],
                "scopes_supported": list(SCOPES),
                "bearer_methods_supported": ["header"],
                "resource_name": "Workout Relay",
            }

        @app.get("/.well-known/oauth-protected-resource/mcp", include_in_schema=False)
        async def resource_metadata_without_slash():
            return resource_metadata(f"{settings.base_url}/mcp")

        @app.get("/.well-known/oauth-protected-resource/mcp/", include_in_schema=False)
        async def resource_metadata_with_slash():
            return resource_metadata(f"{settings.base_url}/mcp/")

        @app.get("/.well-known/oauth-protected-resource", include_in_schema=False)
        async def resource_metadata_at_the_root():
            # For a client given the site address: point it at the connector.
            return resource_metadata(f"{settings.base_url}/mcp")

        app.mount("/mcp", connector.streamable_http_app())

    if probe is not None:
        probe_app = probe.streamable_http_app()
        if settings.mcp_probe_token:
            probe_app = BearerGate(probe_app, settings.mcp_probe_token)
        app.mount("/mcp-probe", probe_app)

    static = Path(__file__).parent / "static"
    assets = {
        "index": (static / "index.html").read_text(),
        "styles": (static / "styles.css").read_text(),
        "script": (static / "app.js").read_text(),
    }

    @app.middleware("http")
    async def behind_the_proxy(request: Request, call_next):
        """Undo two things that break a connector before it can authenticate.

        The platform terminates TLS upstream. Unless the forwarded scheme is
        honoured, the app believes it is serving plain HTTP and emits http://
        redirects, which a client refuses to follow from an https:// page.
        Only an upgrade is applied, never a downgrade.

        Clients also strip the trailing slash from the connector address, and
        a redirect is not a safe answer for a POST that carries a bearer token
        and a body, so /mcp is served directly.
        """
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        if forwarded == "https":
            request.scope["scheme"] = "https"
        if request.scope["path"] == "/mcp":
            request.scope["path"] = "/mcp/"
        return await call_next(request)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["X-Frame-Options"] = "DENY"
        if settings.app_env == "production":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        private_prefixes = (
            "/api/v1/auth",
            "/api/v1/me",
            "/api/v1/account",
            "/api/v1/api-keys",
            "/api/v1/garmin",
            "/api/v1/plans",
            "/api/v1/activities",
            "/mcp",
            "/oauth/consent",
        )
        if request.url.path.startswith(private_prefixes):
            response.headers["Cache-Control"] = "no-store"
        return response

    async def db_session():
        db = database.session()
        try:
            yield db
        finally:
            db.close()

    async def actor(
        credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
        session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
        db: Session = Depends(db_session),
    ) -> Actor:
        if credentials:
            if credentials.scheme.lower() != "bearer" or not credentials.credentials:
                raise api_error(401, "invalid_authorization")
            if credentials.credentials.startswith("wka_") and connector_provider:
                token = await connector_provider.load_access_token(credentials.credentials)
                user = db.get(User, token.subject) if token and token.subject else None
                if user is None:
                    raise api_error(401, "invalid_access_token")
                return Actor(user, "oauth", frozenset(token.scopes))
            result = database.actor_from_api_key(db, credentials.credentials)
            if not result:
                raise api_error(401, "invalid_api_key")
            user, scopes = result
            db.commit()
            return Actor(user, "api_key", frozenset(scopes))
        if session_token:
            result = database.actor_from_session(db, session_token)
            if result:
                user, csrf_hash = result
                return Actor(user, "session", frozenset(SCOPES), csrf_hash)
        raise api_error(401, "authentication_required")

    async def mutation_actor(
        current: Actor = Depends(actor),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> Actor:
        if current.method == "session":
            if not csrf or not hmac.compare_digest(hash_token(csrf), current.csrf_hash or ""):
                raise api_error(403, "csrf_failed")
        elif "plans:write" not in current.scopes:
            raise api_error(403, "scope_required")
        return current

    async def session_mutation_actor(current: Actor = Depends(mutation_actor)) -> Actor:
        if current.method != "session":
            raise api_error(403, "browser_session_required")
        return current

    async def session_actor(current: Actor = Depends(actor)) -> Actor:
        if current.method != "session":
            raise api_error(403, "browser_session_required")
        return current

    def issue_session(response: Response, db: Session, user: User) -> str:
        session_token = opaque_token()
        csrf = opaque_token()
        database.create_browser_session(db, user.id, session_token, csrf, settings.session_days)
        max_age = settings.session_days * 86400
        response.set_cookie(
            SESSION_COOKIE,
            session_token,
            max_age=max_age,
            httponly=True,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        response.set_cookie(
            CSRF_COOKIE,
            csrf,
            max_age=max_age,
            httponly=False,
            secure=settings.cookie_secure,
            samesite="lax",
        )
        return csrf

    def save_connection(
        db: Session, user_id: str, connected: Connected, retention: str
    ) -> None:
        encrypted, expires_at = tokens_store.start(user_id, connected.token_bundle, retention)
        item = db.get(GarminConnection, user_id)
        if item:
            item.encrypted_tokens = encrypted
            item.display_name = connected.display_name
            item.status = "connected"
            item.last_validated_at = now()
            item.retention = retention
            item.visit_expires_at = expires_at
        else:
            db.add(
                GarminConnection(
                    user_id=user_id,
                    encrypted_tokens=encrypted,
                    display_name=connected.display_name,
                    status="connected",
                    retention=retention,
                    visit_expires_at=expires_at,
                )
            )

    @app.post("/", include_in_schema=False)
    async def connector_at_the_root(request: Request):
        """Send a connector that was given the bare domain to the right place.

        People paste the site address rather than the connector address, and
        the client then posts JSON-RPC here and reports that the server
        returned an error. 307 preserves the method and body, so the call
        simply lands on /mcp/. Anything that is not JSON-RPC is refused.
        """
        if connector is None or "json" not in request.headers.get("content-type", ""):
            raise api_error(405, "method_not_allowed")
        return Response(status_code=307, headers={"Location": "/mcp/"})

    @app.get("/", include_in_schema=False)
    async def index():
        return HTMLResponse(
            assets["index"],
            headers={
                "Cache-Control": "no-cache",
                "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
            },
        )

    @app.get("/about", include_in_schema=False)
    async def about_page(request: Request):
        language = page_language(request)
        response = HTMLResponse(about_html(language), headers={"Cache-Control": "no-store"})
        if request.query_params.get("lang") in ("en", "fr"):
            response.set_cookie(LANGUAGE_COOKIE, language, max_age=31536000,
                                secure=settings.cookie_secure, samesite="lax")
        return response

    @app.get("/static/runner.svg", include_in_schema=False)
    async def runner_logo():
        return Response((static / "runner.svg").read_text(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-cache"})

    @app.get("/static/styles.css", include_in_schema=False)
    async def styles():
        return Response(assets["styles"], media_type="text/css", headers={"Cache-Control": "no-cache"})

    @app.get("/static/app.js", include_in_schema=False)
    async def script():
        return Response(
            assets["script"],
            media_type="application/javascript",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/healthz", tags=["system"])
    async def health():
        return {"status": "ok", "garmin_mode": settings.garmin_mode}

    @app.get("/readyz", tags=["system"])
    async def ready():
        with database.session() as readiness_db:
            readiness_db.execute(select(1))
        return {"status": "ready"}

    @app.get("/api/v1/policy", tags=["system"])
    async def policy():
        """The retention periods the privacy notice quotes.

        Served unauthenticated so the notice can state real numbers rather
        than prose, and so they cannot drift from the configuration.
        """
        return {
            "plan_retention_days": settings.plan_retention_days,
            "session_days": settings.session_days,
            "garmin_visit_minutes": settings.garmin_visit_minutes,
            "activity_id_retention_hours": int(ACTIVITY_ACCESS_TTL.total_seconds() / 3600),
        }

    @app.get("/api/v1/plan-schema", tags=["plan format"])
    async def schema_endpoint():
        return plan_schema()

    @app.get("/api/v1/plan-example", tags=["plan format"])
    async def example_endpoint():
        return example_plan()

    @app.get("/api/v1/plan-format", tags=["plan format"])
    async def format_endpoint(language: str = "en"):
        return assistant_instructions("fr" if language == "fr" else "en", settings.base_url)

    @app.post("/api/v1/auth/register", status_code=201, tags=["auth"])
    async def register(body: Credentials, response: Response, request: Request, db: Session = Depends(db_session)):
        key = f"register:{request.client.host if request.client else 'unknown'}"
        if not limiter.allow(key, 10, 3600):
            raise api_error(429, "rate_limited")
        try:
            user = User(
                email=str(body.email).lower(),
                password_hash=hash_password(body.password.get_secret_value()),
            )
            db.add(user)
            db.flush()
            csrf = issue_session(response, db, user)
            database.audit(db, "account.created", user.id)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise api_error(409, "email_exists")
        return {"user": user_json(user), "csrf_token": csrf}

    @app.post("/api/v1/auth/login", tags=["auth"])
    async def login(body: Credentials, response: Response, request: Request, db: Session = Depends(db_session)):
        key = f"login:{request.client.host if request.client else 'unknown'}:{str(body.email).lower()}"
        if not limiter.allow(key, 10, 900):
            raise api_error(429, "rate_limited")
        user = db.scalar(select(User).where(User.email == str(body.email).lower()))
        if not user or not verify_password(body.password.get_secret_value(), user.password_hash):
            raise api_error(401, "invalid_credentials")
        csrf = issue_session(response, db, user)
        database.audit(db, "account.login", user.id)
        db.commit()
        return {"user": user_json(user), "csrf_token": csrf}

    @app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
    async def logout(
        response: Response,
        current: Actor = Depends(session_mutation_actor),
        token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
        db: Session = Depends(db_session),
    ):
        if token:
            database.purge_session(db, token)
        database.audit(db, "account.logout", current.user.id)
        db.commit()
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)

    @app.get("/api/v1/me", tags=["auth"])
    async def me(current: Actor = Depends(actor)):
        return user_json(current.user) | {"auth_method": current.method}

    @app.put("/api/v1/me/language", tags=["auth"])
    async def set_language(
        body: LanguagePreference,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        user = db.get(User, current.user.id)
        user.preferred_language = body.language
        db.commit()
        return user_json(user)

    @app.post("/api/v1/me/password", tags=["auth"])
    async def change_password(
        body: PasswordChange,
        response: Response,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        user = db.get(User, current.user.id)
        if not verify_password(body.current_password.get_secret_value(), user.password_hash):
            raise api_error(401, "invalid_current_password")
        user.password_hash = hash_password(body.new_password.get_secret_value())
        db.execute(delete(BrowserSession).where(BrowserSession.user_id == user.id))
        csrf = issue_session(response, db, user)
        database.audit(db, "account.password_changed", user.id)
        db.commit()
        return {"status": "changed", "csrf_token": csrf}

    @app.delete("/api/v1/account", status_code=204, tags=["auth"])
    async def delete_account(
        body: AccountDeletion,
        response: Response,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        user = db.get(User, current.user.id)
        if not verify_password(body.password.get_secret_value(), user.password_hash):
            raise api_error(401, "invalid_current_password")
        database.audit(db, "account.deleted", user.id)
        db.delete(user)
        db.commit()
        response.delete_cookie(SESSION_COOKIE)
        response.delete_cookie(CSRF_COOKIE)

    @app.get("/oauth/consent", include_in_schema=False)
    async def consent_page(request: Request, session_token: str | None = Cookie(default=None, alias=SESSION_COOKIE)):
        grant = pending_grant(database, request.query_params.get("request", ""))
        language = page_language(request)
        if grant is None:
            return HTMLResponse(consent_html(language, expired=True), status_code=400)
        actor_user = None
        if session_token:
            with database.session() as db:
                found = database.actor_from_session(db, session_token)
                actor_user = found[0].email if found else None
        response = HTMLResponse(
            consent_html(
                language,
                grant=grant,
                email=actor_user,
                csrf=request.cookies.get(CSRF_COOKIE, ""),
            )
        )
        if request.query_params.get("lang") in ("en", "fr"):
            response.set_cookie(LANGUAGE_COOKIE, language, max_age=31536000,
                                secure=settings.cookie_secure, samesite="lax")
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.post("/oauth/consent", include_in_schema=False)
    async def consent_submit(request: Request):
        form = await request.form()
        grant_id = str(form.get("request", ""))
        language = page_language(request, form.get("lang"))
        session_token = request.cookies.get(SESSION_COOKIE)
        grant = pending_grant(database, grant_id)
        if grant is None:
            return HTMLResponse(consent_html(language, expired=True), status_code=400)
        if str(form.get("action")) == "deny":
            target = deny(database, grant_id)
            return Response(status_code=303, headers={"Location": target or settings.base_url})
        user = None
        if session_token:
            with database.session() as db:
                found = database.actor_from_session(db, session_token)
                if found:
                    user = found[0]
                    if not hmac.compare_digest(hash_token(str(form.get("csrf", ""))), found[1]):
                        raise api_error(403, "csrf_failed")
        if user is None:
            # Not signed in yet: authenticate inside the consent page itself.
            key = f"consent-login:{request.client.host if request.client else 'unknown'}"
            if not limiter.allow(key, 10, 900):
                raise api_error(429, "rate_limited")
            email = str(form.get("email", "")).lower()
            password = str(form.get("password", ""))
            with database.session() as db:
                candidate = db.scalar(select(User).where(User.email == email))
                if not candidate or not verify_password(password, candidate.password_hash):
                    return HTMLResponse(
                        consent_html(language, grant=grant, error="invalid_credentials"),
                        status_code=401,
                    )
                response = Response(status_code=303)
                issue_session(response, db, candidate)
                database.audit(db, "account.login", candidate.id, {"via": "consent"})
                db.commit()
            # Re-enter the page with a session so consent is a deliberate second step.
            response.headers["Location"] = f"{settings.base_url}/oauth/consent?request={grant_id}&lang={language}"
            return response
        target = approve(database, grant_id, user.id)
        if target is None:
            return HTMLResponse(consent_html(language, expired=True), status_code=400)
        return Response(status_code=303, headers={"Location": target})

    @app.get("/api/v1/connections", tags=["Connected assistants"])
    async def list_connections(current: Actor = Depends(session_actor)):
        return {
            "items": [
                item | {"created_at": iso(item["created_at"]), "last_used_at": iso(item["last_used_at"])}
                for item in connections(database, current.user.id)
            ]
        }

    @app.delete("/api/v1/connections/{family}", status_code=204, tags=["Connected assistants"])
    async def revoke_connection(family: str, current: Actor = Depends(session_mutation_actor)):
        if not revoke_family(database, current.user.id, family):
            raise api_error(404, "connection_not_found")

    @app.get("/api/v1/api-keys", tags=["API keys"])
    async def list_api_keys(current: Actor = Depends(session_actor), db: Session = Depends(db_session)):
        items = db.scalars(
            select(ApiKey).where(ApiKey.user_id == current.user.id).order_by(ApiKey.created_at.desc())
        ).all()
        return {"items": [api_key_json(item) for item in items]}

    @app.post("/api/v1/api-keys", status_code=201, tags=["API keys"])
    async def create_api_key(
        body: ApiKeyRequest,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        name = body.name.strip()
        if not name:
            raise api_error(422, "api_key_name_required")
        token = opaque_token("wkr_")
        item = ApiKey(
            user_id=current.user.id,
            name=name,
            prefix=token[:12],
            token_hash=hash_token(token),
            scopes=" ".join(dict.fromkeys(body.scopes)),
        )
        db.add(item)
        database.audit(db, "api_key.created", current.user.id, {"key_id": item.id})
        db.commit()
        return api_key_json(item) | {"token": token}

    @app.delete("/api/v1/api-keys/{key_id}", status_code=204, tags=["API keys"])
    async def revoke_api_key(
        key_id: str,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        item = db.get(ApiKey, key_id)
        if not item or item.user_id != current.user.id:
            raise api_error(404, "api_key_not_found")
        db.delete(item)
        database.audit(db, "api_key.revoked", current.user.id, {"key_id": key_id})
        db.commit()

    @app.get("/api/v1/activities", tags=["Activities"])
    async def list_activities(
        limit: int = 5, start: int = 0, sport: str | None = None,
        current: Actor = Depends(actor),
    ):
        if "activities:read" not in current.scopes:
            raise api_error(403, "scope_required")
        try:
            return await drain_thread(activities.list, current.user.id, limit, start, sport)
        except ActivityError as exc:
            raise api_error(exc.status, exc.code) from None

    @app.get("/api/v1/activities/{activity_id}", tags=["Activities"])
    async def get_activity(
        activity_id: str,
        samples: int | None = None,
        laps: bool = True,
        current: Actor = Depends(actor),
    ):
        if "activities:read" not in current.scopes:
            raise api_error(403, "scope_required")
        try:
            return await drain_thread(
                activities.get, current.user.id, activity_id, samples, laps
            )
        except ActivityError as exc:
            raise api_error(exc.status, exc.code) from None

    @app.get("/api/v1/garmin/status", tags=["Garmin"])
    async def garmin_status(current: Actor = Depends(actor), db: Session = Depends(db_session)):
        connection = db.get(GarminConnection, current.user.id)
        return connection_json(connection, settings.garmin_mode, tokens_store)

    @app.post("/api/v1/garmin/connect/start", tags=["Garmin"])
    async def garmin_connect_start(
        body: GarminLogin,
        request: Request,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        key = f"garmin-login:{current.user.id}:{request.client.host if request.client else 'unknown'}"
        if not limiter.allow(key, 5, 900):
            raise api_error(429, "rate_limited")
        password = body.password.get_secret_value()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    gateway.start_login, current.user.id, str(body.email), password
                ),
                timeout=GARMIN_LOGIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # The thread keeps running; any session it completes is simply
            # discarded, and the attempt it may register expires by itself.
            logger.warning("garmin sign-in exceeded %ss", GARMIN_LOGIN_TIMEOUT_SECONDS)
            database.audit(
                db, "garmin.login_failed", current.user.id, {"code": "garmin_login_timeout"}
            )
            db.commit()
            raise api_error(504, "garmin_login_timeout")
        except GarminError as exc:
            database.audit(db, "garmin.login_failed", current.user.id, {"code": exc.code})
            db.commit()
            raise api_error(400, exc.code)
        finally:
            del password
        if isinstance(result, MfaRequired):
            database.audit(db, "garmin.mfa_required", current.user.id)
            db.commit()
            return {
                "status": "mfa_required",
                "attempt_id": result.attempt_id,
                "expires_in": result.expires_in,
            }
        save_connection(db, current.user.id, result, body.retention)
        database.audit(db, "garmin.connected", current.user.id, {"retention": body.retention})
        db.commit()
        return {"status": "connected", "display_name": result.display_name, "retention": body.retention}

    @app.post("/api/v1/garmin/connect/complete", tags=["Garmin"])
    async def garmin_connect_complete(
        body: GarminMfa,
        current: Actor = Depends(session_mutation_actor),
        db: Session = Depends(db_session),
    ):
        code = body.code.get_secret_value()
        try:
            result = await asyncio.to_thread(
                gateway.complete_mfa, current.user.id, body.attempt_id, code
            )
        except GarminError as exc:
            database.audit(db, "garmin.mfa_failed", current.user.id, {"code": exc.code})
            db.commit()
            raise api_error(400, exc.code)
        finally:
            del code
        save_connection(db, current.user.id, result, body.retention)
        database.audit(db, "garmin.connected", current.user.id, {"retention": body.retention})
        db.commit()
        return {"status": "connected", "display_name": result.display_name, "retention": body.retention}

    @app.delete("/api/v1/garmin/connection", status_code=204, tags=["Garmin"])
    async def garmin_disconnect(
        current: Actor = Depends(session_mutation_actor), db: Session = Depends(db_session)
    ):
        db.execute(delete(GarminConnection).where(GarminConnection.user_id == current.user.id))
        tokens_store.forget(current.user.id)
        database.audit(db, "garmin.disconnected", current.user.id)
        db.commit()

    @app.post("/api/v1/plans/validate", tags=["Plans"])
    async def validate_endpoint(
        plan: dict,
        request: Request,
        current: Actor = Depends(mutation_actor),
    ):
        try:
            check_plan(plan, settings.max_plan_bytes)
        except PlanRejected as rejected:
            raise plan_error(rejected)
        return {
            "valid": True,
            "plan_id": plan["plan_id"],
            "workout_count": len(plan["workouts"]),
        }

    @app.post("/api/v1/plans", status_code=202, tags=["Plans"])
    async def upload_plan(
        plan: dict,
        request: Request,
        current: Actor = Depends(mutation_actor),
        db: Session = Depends(db_session),
    ):
        try:
            accepted = queue_plan(db, database, current.user.id, plan, settings.max_plan_bytes)
        except PlanRejected as rejected:
            raise plan_error(rejected)
        queue_event.set()
        return accepted.as_dict()

    @app.get("/api/v1/plans", tags=["Plans"])
    async def list_plans(current: Actor = Depends(actor), db: Session = Depends(db_session)):
        if current.method != "session" and "plans:read" not in current.scopes:
            raise api_error(403, "scope_required")
        items = db.scalars(
            select(PlanSubmission)
            .where(PlanSubmission.user_id == current.user.id)
            .order_by(PlanSubmission.created_at.desc())
            .limit(50)
        ).all()
        return {"items": [submission_json(item) for item in items]}

    @app.delete("/api/v1/plans", tags=["Plans"])
    async def clear_plan_history(
        current: Actor = Depends(session_mutation_actor), db: Session = Depends(db_session)
    ):
        """Forget finished submissions, keeping what prevents duplicates.

        The workout links record which Garmin workout each id became. Deleting
        those would make the next send create a second copy of every workout
        rather than updating the existing one, so history is cleared and the
        links stay.
        """
        removed = db.execute(
            delete(PlanSubmission).where(
                PlanSubmission.user_id == current.user.id,
                PlanSubmission.status.in_(("completed", "failed")),
            )
        ).rowcount
        remaining = db.scalar(
            select(func.count())
            .select_from(PlanSubmission)
            .where(PlanSubmission.user_id == current.user.id)
        )
        database.audit(db, "plans.history_cleared", current.user.id, {"removed": removed})
        db.commit()
        return {"removed": removed, "in_progress": remaining}

    @app.get("/api/v1/plans/{submission_id}", tags=["Plans"])
    async def get_plan_status(
        submission_id: str,
        current: Actor = Depends(actor),
        db: Session = Depends(db_session),
    ):
        if current.method != "session" and "plans:read" not in current.scopes:
            raise api_error(403, "scope_required")
        item = db.get(PlanSubmission, submission_id)
        if not item or item.user_id != current.user.id:
            raise api_error(404, "plan_not_found")
        return submission_json(item)

    def custom_openapi() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        specification = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        specification["servers"] = [{"url": settings.base_url}]
        schemas = specification.setdefault("components", {}).setdefault("schemas", {})
        schemas["WorkoutPlan"] = openapi_plan_schema()
        request_schema = {"$ref": "#/components/schemas/WorkoutPlan"}
        for path in ("/api/v1/plans/validate", "/api/v1/plans"):
            specification["paths"][path]["post"]["requestBody"]["content"][
                "application/json"
            ]["schema"] = request_schema
        app.openapi_schema = specification
        return specification

    app.openapi = custom_openapi

    return app


async def upload_worker(
    database: Database,
    tokens_store: GarminTokens,
    gateway: Gateway,
    locks: UserLockPool,
    wake: asyncio.Event,
    stop: asyncio.Event,
) -> None:
    while not stop.is_set():
        wake.clear()
        while not stop.is_set():
            try:
                processed = await process_next_submission(database, tokens_store, gateway, locks)
            except Exception:
                # Never let one bad job or a database blip end the worker;
                # interrupted jobs are requeued at the next startup.
                logger.exception("upload worker iteration failed")
                processed = False
            if not processed:
                break
        if stop.is_set():
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass


async def process_next_submission(
    database: Database,
    tokens_store: GarminTokens,
    gateway: Gateway,
    locks: UserLockPool,
) -> bool:
    with database.upload_owner() as assert_owned:
        if assert_owned is None:
            return False
        return await _process_owned_submission(database, tokens_store, gateway, locks, assert_owned)


async def _process_owned_submission(database, tokens_store, gateway, locks, assert_owned) -> bool:
    with database.session() as db:
        # Only the exclusive owner may recover interrupted submissions. Startup
        # alone is not evidence that the previous deployment has stopped.
        db.execute(update(PlanSubmission).where(PlanSubmission.status == "processing").values(status="queued"))
        db.commit()
        submission = db.scalar(
            select(PlanSubmission)
            .where(PlanSubmission.status == "queued")
            .order_by(PlanSubmission.created_at.asc())
            .limit(1)
        )
        if not submission:
            return False
        submission.status = "processing"
        db.commit()
        connection = db.get(GarminConnection, submission.user_id)
        if not connection or connection.status != "connected":
            submission.status = "failed"
            submission.completed_at = now()
            submission.result = json.dumps({"status": "failed", "code": "garmin_not_connected"})
            database.audit(
                db,
                "plan.failed",
                submission.user_id,
                {"submission_id": submission.id, "code": "garmin_not_connected"},
            )
            db.commit()
            return True
        plan = json.loads(submission.content)
        async with locks.get(submission.user_id):
            await process_plan(
                db,
                database,
                tokens_store,
                gateway,
                submission.user_id,
                connection,
                submission,
                plan,
                assert_owned,
            )
        return True


async def process_plan(
    db: Session,
    database: Database,
    tokens_store: GarminTokens,
    gateway: Gateway,
    user_id: str,
    connection: GarminConnection,
    submission: PlanSubmission,
    plan: dict,
    assert_owned,
) -> dict:
    counts = {"created": 0, "updated": 0, "skipped": 0}
    details = []
    try:
        tokens = tokens_store.read(connection)
        garmin = None
        for workout_data in plan["workouts"]:
            assert_owned()
            digest = content_hash(workout_data)
            operation = db.scalar(select(WorkoutOperation).where(
                WorkoutOperation.user_id == user_id,
                WorkoutOperation.workout_key == workout_data["id"],
            ))
            progress = json.loads(operation.progress) if operation else {}
            link = db.scalar(
                select(WorkoutLink).where(
                    WorkoutLink.user_id == user_id,
                    WorkoutLink.workout_key == workout_data["id"],
                )
            )
            if operation and operation.content_hash != digest and not replaceable(progress, operation, link):
                raise GarminError("garmin_prior_upload_unresolved")
            settled = progress.get("stage", "completed") == "completed"
            if link and link.content_hash == digest and settled and progress.get("cleanup") != "pending":
                counts["skipped"] += 1
                details.append({"id": workout_data["id"], "action": "skipped"})
                continue
            # Remote identity lives in the journal, which can be ahead of the
            # link when a write completed before its local commit.
            if progress.get("workout_id"):
                existing = {
                    "garmin_workout_id": progress["workout_id"],
                    "garmin_schedule_id": progress.get("schedule_id"),
                    "scheduled_date": progress.get("scheduled_date"),
                }
            elif link:
                existing = {
                    "garmin_workout_id": link.garmin_workout_id,
                    "garmin_schedule_id": link.garmin_schedule_id,
                    "scheduled_date": link.scheduled_date,
                }
            else:
                existing = None
            if operation is None:
                operation = WorkoutOperation(user_id=user_id, workout_key=workout_data["id"], content_hash=digest)
                db.add(operation)
            if not progress or operation.content_hash != digest:
                progress = restart_progress(progress)
                operation.content_hash = digest
                operation.progress = json.dumps(progress)
            db.commit()
            operation_id = operation.id
            checkpoint = journal_writer(database, tokens_store, user_id, operation_id, assert_owned)
            workout = build_workout(workout_data)

            if garmin is None:
                garmin = await drain_thread(gateway.open_session, tokens)
            if link and link.content_hash == digest and settled:
                # Nothing to upload; only the marker cleanup is outstanding.
                await clean_up_marker(garmin, workout, progress, checkpoint)  # noqa: E501
                counts["skipped"] += 1
                details.append({"id": workout_data["id"], "action": "skipped"})
                continue
            published = await drain_thread(
                garmin.publish,
                workout,
                workout_data["date"],
                existing,
                progress=progress,
                checkpoint=checkpoint,
            )
            assert_owned()
            tokens = published.token_bundle
            stored = tokens_store.remember(connection, tokens)
            if stored is not None:
                connection.encrypted_tokens = stored
            if link:
                action = "updated"
                link.content_hash = digest
                link.garmin_workout_id = published.workout_id
                link.garmin_schedule_id = published.schedule_id
                link.scheduled_date = workout_data["date"]
            else:
                action = "created"
                db.add(
                    WorkoutLink(
                        user_id=user_id,
                        workout_key=workout_data["id"],
                        content_hash=digest,
                        garmin_workout_id=published.workout_id,
                        garmin_schedule_id=published.schedule_id,
                        scheduled_date=workout_data["date"],
                    )
                )
            counts[action] += 1
            details.append(
                {
                    "id": workout_data["id"],
                    "action": action,
                    "garmin_workout_id": published.workout_id,
                }
            )
            db.commit()
            # Only once the completed operation and the link are committed.
            await clean_up_marker(garmin, workout, checkpoint.state, checkpoint)
        submission.status = "completed"
        submission.completed_at = now()
        submission.result = json.dumps({"counts": counts, "workouts": details})
        database.audit(db, "plan.completed", user_id, {"submission_id": submission.id, **counts})
        db.commit()
        return {
            "id": submission.id,
            "status": "completed",
            "counts": counts,
            "workouts": details,
        }
    except GarminError as exc:
        db.rollback()
        assert_owned()
        if exc.code == "garmin_reauthentication_required":
            connection.status = "reauthentication_required"
        result = {"status": "failed", "code": exc.code, "counts": counts, "workouts": details}
        submission.status = "failed"
        submission.completed_at = now()
        submission.result = json.dumps(result)
        database.audit(db, "plan.failed", user_id, {"submission_id": submission.id, "code": exc.code})
        db.commit()
        return {"id": submission.id, **result}
    except Exception:
        logger.exception("plan upload failed")
        db.rollback()
        assert_owned()
        result = {"status": "failed", "code": "internal_upload_error", "counts": counts}
        submission.status = "failed"
        submission.completed_at = now()
        submission.result = json.dumps(result)
        database.audit(db, "plan.failed", user_id, {"submission_id": submission.id, "code": result["code"]})
        db.commit()
        return {"id": submission.id, **result}


AMBIGUOUS_STAGES = ("creating", "scheduling")


def replaceable(progress: dict, operation: WorkoutOperation, link: WorkoutLink | None) -> bool:
    """May a changed payload replace this unfinished operation?

    Yes once the remote identity is known: both the update PUT and the
    unschedule DELETE repeat safely. An ambiguous creation or scheduling must
    be reconciled first, and older journals without a recorded identity keep
    the conservative behaviour.
    """
    stage = progress.get("stage", "completed")
    if stage in AMBIGUOUS_STAGES:
        return False
    if stage == "ready":
        return True
    if progress.get("workout_id"):
        return True
    # No journalled identity: only a link proves where the last version went.
    return stage == "completed" and link is not None and link.content_hash == operation.content_hash


def restart_progress(previous: dict) -> dict:
    """Begin a new payload attempt, keeping remote identity and cleanup debt."""
    carried = {
        key: previous[key]
        for key in ("workout_id", "schedule_id", "scheduled_date", "cleanup", "marker")
        if previous.get(key) is not None
    }
    carried.setdefault("marker", f"[Workout Relay:{uuid4()}]")
    carried["stage"] = "ready"
    return carried


def journal_writer(database: Database, tokens_store: GarminTokens, user_id: str, operation_id: str, assert_owned):
    """Persist journal progress, and remember the last state written.

    Callers must read progress back from `checkpoint.state` rather than the
    request's Session, whose copy of the row is stale: the journal is written
    by a different Session and `expire_on_commit` is off.
    """
    written: dict = {}

    def checkpoint(state: dict, refreshed_tokens: str) -> None:
        assert_owned()
        # Runs in the Garmin thread: never share the request's Session.
        with database.session() as journal_db:
            journal = journal_db.get(WorkoutOperation, operation_id)
            connected = journal_db.get(GarminConnection, user_id)
            if journal is None or connected is None:
                raise GarminError("garmin_not_connected")
            journal.progress = json.dumps(state)
            stored = tokens_store.remember(connected, refreshed_tokens)
            if stored is not None:
                connected.encrypted_tokens = stored
            journal_db.commit()
        written.clear()
        written.update(state)

    checkpoint.state = written
    return checkpoint


async def clean_up_marker(garmin, workout, progress: dict, checkpoint) -> None:
    """Best-effort removal of a recovery marker from a created workout.

    A failure here never fails an upload that already succeeded, and never
    repeats a creation or scheduling request; the next submission retries it.
    """
    if progress.get("cleanup") != "pending" or not progress.get("workout_id"):
        return
    try:
        await drain_thread(
            garmin.cleanup_marker,
            workout,
            progress["workout_id"],
            progress=progress,
            checkpoint=checkpoint,
        )
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("marker cleanup failed; retrying on a later submission", exc_info=True)


async def drain(task: asyncio.Task, timeout: float | None = None):
    """Wait for `task` even while this coroutine is being cancelled.

    `shield()` alone is not enough: the cancellation still escapes the await,
    which would release upload ownership while a Garmin request runs on.
    """
    loop = asyncio.get_running_loop()
    deadline = None if timeout is None else loop.time() + timeout
    cancelled = False
    while not task.done():
        remaining = None if deadline is None else deadline - loop.time()
        if remaining is not None and remaining <= 0:
            break
        try:
            # asyncio.wait() never cancels the task it is waiting on.
            await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            cancelled = True
    if not task.done():
        # Deadline reached: the caller decides, and cancellation is not
        # re-raised because shutdown must continue either way.
        return None
    if cancelled:
        # Retrieve any exception before propagating cancellation.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


async def drain_thread(function, *args, **kwargs):
    """Keep ownership until an already-started thread actually exits."""
    return await drain(asyncio.create_task(asyncio.to_thread(function, *args, **kwargs)))


async def activity_access_cleanup_worker(database: Database, stop: asyncio.Event) -> None:
    """Remove expired activity-ID authorizations even when the site is idle."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=60)
        except TimeoutError:
            try:
                await asyncio.to_thread(purge_expired_activity_access, database)
            except Exception:
                # Do not attach upstream or database exception text to logs.
                logger.warning("expired activity authorization cleanup failed")


def api_error(status: int, code: str) -> HTTPException:
    return HTTPException(status, detail={"code": code})


CONSENT_TEXT = {
    "en": {
        "title": "Connect an assistant",
        "intro": "{client} is asking to connect to your Workout Relay account.",
        "allows": "If you approve, it will be able to:",
        "plans:read": "read your plan format, submissions and Garmin connection status",
        "activities:read": "ask Garmin for completed activity data, which may reach Workout Relay with GPS coordinates; remove coordinates and route data on the Workout Relay server, then share the filtered metrics (including heart rate, pace and distance) with this assistant",
        "plans:write": "send workout plans to your Garmin calendar",
        "never": "It will never receive your Garmin password, your Garmin session tokens, or your Workout Relay password.",
        "revoke": "You can revoke this at any time under Assistants, Connected assistants.",
        "signin": "Sign in to continue",
        "email": "Email",
        "password": "Password",
        "continue": "Continue",
        "approve": "Approve",
        "deny": "Deny",
        "as": "Signed in as {email}",
        "expired": "This request has expired or was already used. Start the connection again from the assistant.",
        "invalid_credentials": "Incorrect email or password.",
    },
    "fr": {
        "title": "Connecter un assistant",
        "intro": "{client} demande à se connecter à votre compte Workout Relay.",
        "allows": "Si vous acceptez, il pourra :",
        "plans:read": "lire votre format de plan, vos envois et l'état de la connexion Garmin",
        "activities:read": "demander à Garmin les activités terminées, qui peuvent parvenir à Workout Relay avec des coordonnées GPS ; supprimer les coordonnées et le tracé sur le serveur Workout Relay, puis transmettre à cet assistant les mesures filtrées (dont fréquence cardiaque, allure et distance)",
        "plans:write": "envoyer des plans d'entraînement vers votre calendrier Garmin",
        "never": "Il ne recevra jamais votre mot de passe Garmin, vos jetons de session Garmin, ni votre mot de passe Workout Relay.",
        "revoke": "Vous pouvez révoquer cet accès à tout moment dans Assistants, Assistants connectés.",
        "signin": "Connectez-vous pour continuer",
        "email": "E-mail",
        "password": "Mot de passe",
        "continue": "Continuer",
        "approve": "Autoriser",
        "deny": "Refuser",
        "as": "Connecté en tant que {email}",
        "expired": "Cette demande a expiré ou a déjà été utilisée. Relancez la connexion depuis l'assistant.",
        "invalid_credentials": "E-mail ou mot de passe incorrect.",
    },
}

CONSENT_STYLE = (
    ".language-choices{display:flex;justify-content:flex-end;gap:6px;margin-bottom:24px;flex-wrap:wrap}"
    ".language-choices a{padding:10px 12px;border:1px solid #dce2e8;border-radius:99px;color:#344454;text-decoration:none;font-size:14px}"
    ".language-choices a[aria-current=true]{background:#eaf5fc;color:#005e96;border-color:#0076bb}"
    "a:focus-visible,button:focus-visible,input:focus-visible{outline:3px solid #0076bb;outline-offset:3px}"
    "body{margin:0;padding:24px 16px;background:#f4f6f8;color:#17212b;"
    "font:16px/1.5 ui-sans-serif,system-ui,sans-serif}"
    "main{max-width:420px;margin:0 auto;background:#ffffff;border:1px solid #dce2e8;"
    "border-radius:18px;padding:24px}"
    "h1{font-size:22px;margin:0 0 16px}ul{padding-left:20px}li{margin-bottom:6px}"
    "label{display:block;font-size:13px;font-weight:700;margin-bottom:12px}"
    "input{width:100%;min-height:47px;padding:0 13px;font:inherit;border:1px solid #dce2e8;"
    "border-radius:10px;box-sizing:border-box;margin-top:6px}"
    "button{min-height:47px;width:100%;font:inherit;font-weight:800;border-radius:10px;"
    "border:1px solid transparent;cursor:pointer;margin-top:10px}"
    ".primary{background:#0076bb;color:#fff}.ghost{background:transparent;border-color:#dce2e8;color:#17212b}"
    ".muted{color:#596775;font-size:13px}.error{color:#ad352d;font-size:13px}"
)


def _issuer_usable(base_url: str) -> bool:
    from urllib.parse import urlsplit

    parts = urlsplit(base_url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" or host == "localhost" or host.startswith("127.0.0.1")


def consent_html(
    language: str,
    grant: dict | None = None,
    email: str | None = None,
    csrf: str = "",
    error: str | None = None,
    expired: bool = False,
) -> str:
    """The consent screen, rendered server-side so it needs no script."""
    language = language if language in ("en", "fr") else "en"
    text = CONSENT_TEXT[language]

    def esc(value: str) -> str:
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    head = (
        f'<!doctype html><html lang="{language}"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{esc(text['title'])}</title><style>{CONSENT_STYLE}</style></head><body><main>"
    )
    head += language_links("/oauth/consent", language, **({"request": grant["id"]} if grant else {}))
    if expired or grant is None:
        return head + f"<h1>{esc(text['title'])}</h1><p>{esc(text['expired'])}</p></main></body></html>"

    scopes = "".join(
        f"<li>{esc(text.get(scope, scope))}</li>" for scope in grant["scopes"] or ["plans:read"]
    )
    body = [
        f"<h1>{esc(text['title'])}</h1>",
        f"<p>{esc(text['intro'].format(client=grant['client_name']))}</p>",
        f"<p class='muted'>{esc(text['allows'])}</p><ul>{scopes}</ul>",
        f"<p class='muted'>{esc(text['never'])}</p>",
        f"<p class='muted'>{esc(text['revoke'])}</p>",
    ]
    if error:
        body.append(f"<p class='error'>{esc(text.get(error, error))}</p>")
    body.append(f"<form method='post' action='/oauth/consent'>")
    body.append(f"<input type='hidden' name='request' value='{esc(grant['id'])}'>")
    body.append(f"<input type='hidden' name='lang' value='{language}'>")
    if email:
        body.append(f"<p class='muted'>{esc(text['as'].format(email=email))}</p>")
        body.append(f"<input type='hidden' name='csrf' value='{esc(csrf)}'>")
        body.append(f"<button class='primary' name='action' value='approve'>{esc(text['approve'])}</button>")
    else:
        body.append(f"<p class='muted'>{esc(text['signin'])}</p>")
        body.append(
            f"<label>{esc(text['email'])}<input name='email' type='email' autocomplete='email' required></label>"
        )
        body.append(
            f"<label>{esc(text['password'])}<input name='password' type='password' "
            "autocomplete='current-password' required></label>"
        )
        body.append(f"<button class='primary' name='action' value='login'>{esc(text['continue'])}</button>")
    body.append(f"<button class='ghost' name='action' value='deny' formnovalidate>{esc(text['deny'])}</button>")
    body.append("</form>")
    return head + "".join(body) + "</main></body></html>"


def plan_error(rejected: PlanRejected) -> HTTPException:
    """Keep the REST status codes the mobile client already understands."""
    if rejected.code == "plan_invalid":
        return HTTPException(422, detail={"code": rejected.code, "errors": rejected.errors})
    if rejected.code == "plan_too_large":
        return api_error(413, rejected.code)
    return api_error(409, rejected.code)


def check_size(plan: dict, maximum: int) -> None:
    if len(json.dumps(plan, separators=(",", ":")).encode()) > maximum:
        raise api_error(413, "plan_too_large")


def user_json(user: User) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "preferred_language": user.preferred_language,
    }


def api_key_json(item: ApiKey) -> dict:
    return {
        "id": item.id,
        "name": item.name,
        "prefix": item.prefix,
        "scopes": item.scopes.split(),
        "created_at": iso(item.created_at),
        "last_used_at": iso(item.last_used_at),
    }


def connection_json(
    item: GarminConnection | None, mode: str, tokens_store: GarminTokens | None = None
) -> dict:
    if not item:
        return {"connected": False, "status": "disconnected", "mode": mode, "retention": None}
    retention = item.retention or "persistent"
    live = tokens_store.live(item) if tokens_store else item.status == "connected"
    status = item.status
    if retention == VISIT and item.status == "connected" and not live:
        # The window the person agreed to has closed; say so plainly rather
        # than reporting a connection that cannot reach Garmin.
        status = "visit_expired"
    return {
        "connected": live,
        "status": status,
        "display_name": item.display_name,
        # created_at is set when the connection is established and survives a
        # re-authentication, so it reads as "connected since"; last_validated_at
        # moves each time the Garmin sign-in is repeated.
        "connected_at": iso(item.created_at),
        "last_validated_at": iso(item.last_validated_at),
        "retention": retention,
        "visit_expires_at": iso(tokens_store.expires_at(item)) if tokens_store else None,
        "mode": mode,
    }


def submission_json(item: PlanSubmission) -> dict:
    return {
        "id": item.id,
        "plan_id": item.plan_id,
        "title": item.title,
        "status": item.status,
        "result": json.loads(item.result),
        "created_at": iso(item.created_at),
        "completed_at": iso(item.completed_at),
    }


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def openapi_plan_schema() -> dict:
    schema = plan_schema()
    schema.pop("$schema", None)
    schema.pop("$id", None)

    def rewrite(value):
        if isinstance(value, dict):
            return {
                key: (
                    item.replace(
                        "#/$defs/", "#/components/schemas/WorkoutPlan/$defs/"
                    )
                    if key == "$ref" and isinstance(item, str)
                    else rewrite(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        return value

    return rewrite(schema)


app = create_app()
