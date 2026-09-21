"""OAuth authorization server for assistant connectors.

This authorizes an *assistant platform* to act for a Workout Relay account. It
is unrelated to Garmin authentication: Garmin credentials and tokens never
leave the server, and no scope here grants access to them.

The MCP SDK mounts the protocol endpoints (registration, authorize, token,
revocation) and calls into this provider for storage and decisions. Consent
itself is ours: `authorize` parks the request and sends the person to a page
where they log in and approve, which is the only place a grant is created.

Tokens are stored as SHA-256 hashes, like API keys, so the database never
holds a usable credential.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken as OAuthTokenResponse
from sqlalchemy import select
from uuid import uuid4

from .database import Database, OAuthClient, OAuthGrant, OAuthToken, now
from .security import hash_token, opaque_token

logger = logging.getLogger(__name__)

SCOPES = ("plans:read", "plans:write")
CONSENT_PATH = "/oauth/consent"
AUTHORIZATION_CODE_TTL = timedelta(minutes=10)
ACCESS_TOKEN_TTL = timedelta(hours=1)
CONSENT_TTL = timedelta(minutes=15)


def scope_list(scopes: str | None) -> list[str]:
    return [scope for scope in (scopes or "").split() if scope]


class RelayOAuthProvider(OAuthAuthorizationServerProvider):
    """Storage and decisions for the SDK's OAuth endpoints."""

    def __init__(self, database: Database, base_url: str):
        self.database = database
        self.base_url = base_url.rstrip("/")

    # -- clients ---------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self.database.session() as db:
            record = db.get(OAuthClient, client_id)
            if record is None:
                return None
            return OAuthClientInformationFull.model_validate_json(record.document)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with self.database.session() as db:
            db.merge(
                OAuthClient(
                    client_id=client_info.client_id,
                    secret_hash=hash_token(client_info.client_secret)
                    if client_info.client_secret
                    else None,
                    name=(client_info.client_name or "")[:200],
                    document=client_info.model_dump_json(),
                )
            )
            self.database.audit(db, "oauth.client_registered", None, {"client_id": client_info.client_id})
            db.commit()

    # -- authorization ---------------------------------------------------
    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Park the request and send the person to the consent page.

        No grant exists until someone logs in and approves, so an authorize
        call on its own can never produce a usable code.
        """
        with self.database.session() as db:
            grant = OAuthGrant(
                client_id=client.client_id,
                stage="pending",
                scopes=" ".join(params.scopes or []),
                code_challenge=params.code_challenge or "",
                redirect_uri=str(params.redirect_uri),
                redirect_uri_explicit=params.redirect_uri_provided_explicitly,
                resource=params.resource,
                state=params.state,
                expires_at=now() + CONSENT_TTL,
            )
            db.add(grant)
            db.commit()
            return f"{self.base_url}{CONSENT_PATH}?request={grant.id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        with self.database.session() as db:
            grant = db.scalar(
                select(OAuthGrant).where(OAuthGrant.code_hash == hash_token(authorization_code))
            )
            if grant is None or grant.client_id != client.client_id:
                return None
            if grant.stage != "issued" or _expired(grant):
                if grant.stage == "used":
                    # A replayed code means the real one may have leaked.
                    logger.warning("authorization code replayed for client %s", client.client_id)
                return None
            return AuthorizationCode(
                code=authorization_code,
                scopes=scope_list(grant.scopes),
                expires_at=_epoch(grant.expires_at),
                client_id=grant.client_id,
                code_challenge=grant.code_challenge,
                redirect_uri=grant.redirect_uri,
                redirect_uri_provided_explicitly=grant.redirect_uri_explicit,
                resource=grant.resource,
                subject=grant.user_id,
            )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthTokenResponse:
        with self.database.session() as db:
            grant = db.scalar(
                select(OAuthGrant).where(OAuthGrant.code_hash == hash_token(authorization_code.code))
            )
            if grant is None or grant.stage != "issued" or _expired(grant):
                raise ValueError("invalid_grant")
            grant.stage = "used"
            tokens = _issue(db, self.database, grant.user_id, client.client_id,
                            scope_list(grant.scopes), grant.resource, family=str(uuid4()))
            self.database.audit(db, "oauth.connected", grant.user_id, {"client_id": client.client_id})
            db.commit()
            return tokens

    # -- tokens ----------------------------------------------------------
    async def load_access_token(self, token: str) -> AccessToken | None:
        with self.database.session() as db:
            record = db.get(OAuthToken, hash_token(token))
            if record is None or record.kind != "access" or not _usable(record):
                return None
            record.last_used_at = now()
            db.commit()
            return AccessToken(
                token=token,
                client_id=record.client_id,
                scopes=scope_list(record.scopes),
                expires_at=_epoch(record.expires_at),
                resource=record.resource,
                subject=record.user_id,
            )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        with self.database.session() as db:
            record = db.get(OAuthToken, hash_token(refresh_token))
            if record is None or record.kind != "refresh" or not _usable(record):
                return None
            if record.client_id != client.client_id:
                return None
            return RefreshToken(
                token=refresh_token,
                client_id=record.client_id,
                scopes=scope_list(record.scopes),
                expires_at=_epoch(record.expires_at),
                resource=record.resource,
                subject=record.user_id,
            )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthTokenResponse:
        with self.database.session() as db:
            record = db.get(OAuthToken, hash_token(refresh_token.token))
            if record is None or not _usable(record):
                raise ValueError("invalid_grant")
            granted = scope_list(record.scopes)
            narrowed = [scope for scope in scopes if scope in granted] if scopes else granted
            # Rotate: the presented refresh token is spent, and the family
            # carries over so one revocation still stops everything.
            record.revoked_at = now()
            tokens = _issue(db, self.database, record.user_id, client.client_id,
                            narrowed, record.resource, family=record.family)
            db.commit()
            return tokens

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        with self.database.session() as db:
            record = db.get(OAuthToken, hash_token(token.token))
            if record is None:
                return
            # Revoking either half of a pair ends the whole connection.
            for sibling in db.scalars(
                select(OAuthToken).where(OAuthToken.family == record.family)
            ).all():
                sibling.revoked_at = sibling.revoked_at or now()
            self.database.audit(db, "oauth.revoked", record.user_id, {"client_id": record.client_id})
            db.commit()


def approve(database: Database, grant_id: str, user_id: str) -> str | None:
    """Turn an approved consent into a redirect carrying a fresh code."""
    with database.session() as db:
        grant = db.get(OAuthGrant, grant_id)
        if grant is None or grant.stage != "pending" or _expired(grant):
            return None
        code = opaque_token("wkc_")
        grant.user_id = user_id
        grant.stage = "issued"
        grant.code_hash = hash_token(code)
        grant.expires_at = now() + AUTHORIZATION_CODE_TTL
        database.audit(db, "oauth.consent_granted", user_id, {"client_id": grant.client_id})
        db.commit()
        return construct_redirect_uri(grant.redirect_uri, code=code, state=grant.state)


def deny(database: Database, grant_id: str) -> str | None:
    with database.session() as db:
        grant = db.get(OAuthGrant, grant_id)
        if grant is None or grant.stage != "pending":
            return None
        grant.stage = "denied"
        db.commit()
        return construct_redirect_uri(
            grant.redirect_uri, error="access_denied", state=grant.state
        )


def pending_grant(database: Database, grant_id: str):
    with database.session() as db:
        grant = db.get(OAuthGrant, grant_id)
        if grant is None or grant.stage != "pending" or _expired(grant):
            return None
        client = db.get(OAuthClient, grant.client_id)
        name = client.name if client and client.name else grant.client_id
        return {"id": grant.id, "client_name": name, "scopes": scope_list(grant.scopes)}


def connections(database: Database, user_id: str) -> list[dict]:
    """Live connections for the account, one row per token family."""
    with database.session() as db:
        rows = db.scalars(
            select(OAuthToken)
            .where(OAuthToken.user_id == user_id, OAuthToken.revoked_at.is_(None))
            .order_by(OAuthToken.created_at.desc())
        ).all()
        clients = {c.client_id: c for c in db.scalars(select(OAuthClient)).all()}
        families: dict[str, dict] = {}
        for row in rows:
            entry = families.setdefault(
                row.family,
                {
                    "id": row.family,
                    "client_id": row.client_id,
                    "client_name": (clients.get(row.client_id).name if clients.get(row.client_id) else "")
                    or row.client_id,
                    "scopes": scope_list(row.scopes),
                    "created_at": row.created_at,
                    "last_used_at": row.last_used_at,
                },
            )
            if row.last_used_at and (
                entry["last_used_at"] is None or row.last_used_at > entry["last_used_at"]
            ):
                entry["last_used_at"] = row.last_used_at
        return list(families.values())


def revoke_family(database: Database, user_id: str, family: str) -> bool:
    with database.session() as db:
        rows = db.scalars(
            select(OAuthToken).where(OAuthToken.family == family, OAuthToken.user_id == user_id)
        ).all()
        if not rows:
            return False
        for row in rows:
            row.revoked_at = row.revoked_at or now()
        database.audit(db, "oauth.revoked", user_id, {"client_id": rows[0].client_id})
        db.commit()
        return True


def _issue(db, database: Database, user_id: str, client_id: str, scopes: list[str],
           resource: str | None, family: str) -> OAuthTokenResponse:
    access = opaque_token("wka_")
    refresh = opaque_token("wkr_")
    expires_at = now() + ACCESS_TOKEN_TTL
    db.add(
        OAuthToken(
            token_hash=hash_token(access), family=family, kind="access", user_id=user_id,
            client_id=client_id, scopes=" ".join(scopes), resource=resource, expires_at=expires_at,
        )
    )
    db.add(
        OAuthToken(
            token_hash=hash_token(refresh), family=family, kind="refresh", user_id=user_id,
            client_id=client_id, scopes=" ".join(scopes), resource=resource, expires_at=None,
        )
    )
    return OAuthTokenResponse(
        access_token=access,
        token_type="Bearer",
        expires_in=int(ACCESS_TOKEN_TTL.total_seconds()),
        scope=" ".join(scopes),
        refresh_token=refresh,
    )


def _utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treating those as local time makes a
    fresh code look hours old, so normalize before any comparison."""
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _epoch(value: datetime | None) -> int | None:
    return int(_utc(value).timestamp()) if value is not None else None


def _usable(record: OAuthToken) -> bool:
    if record.revoked_at is not None:
        return False
    return record.expires_at is None or _utc(record.expires_at) > now()


def _expired(grant: OAuthGrant) -> bool:
    return _utc(grant.expires_at) <= now()
