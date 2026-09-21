"""The assistant connector: MCP tools over an authorized Workout Relay account.

Every tool acts for the account that granted the connection, identified by the
access token's subject. Garmin credentials are never reachable from here: the
connector can queue a plan, and the existing worker does the Garmin work with
tokens that stay server-side.

Results are plain JSON. Assistants parse that reliably, which a delivery
experiment confirmed on both ChatGPT and Claude, whereas binary attachments
did not survive on every client.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import ProviderTokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import AnyHttpUrl

from .config import Settings
from .database import Database, GarminConnection
from .mcp_probe import allowed_hosts
from .oauth import SCOPES, RelayOAuthProvider
from .plans import assistant_instructions, example_plan, plan_schema
from .submissions import Accepted, PlanRejected, check_plan, queue_plan, recent_submissions

logger = logging.getLogger(__name__)


class NotAuthorized(Exception):
    pass


def _actor(required: str) -> str:
    """The account this call acts for, or a refusal naming the missing scope."""
    token = get_access_token()
    if token is None or not token.subject:
        raise NotAuthorized("authentication_required")
    if required not in token.scopes:
        raise NotAuthorized(f"scope_required: {required}")
    return token.subject


def _error(code: str, **extra) -> str:
    return json.dumps({"ok": False, "code": code, **extra}, indent=2)


def _ok(payload: dict) -> str:
    return json.dumps({"ok": True, **payload}, indent=2)


def submission_json(item) -> dict:
    return {
        "id": item.id,
        "plan_id": item.plan_id,
        "title": item.title,
        "status": item.status,
        "result": json.loads(item.result),
        "created_at": item.created_at.isoformat() if item.created_at else None,
        "completed_at": item.completed_at.isoformat() if item.completed_at else None,
    }


def build_connector(
    settings: Settings,
    database: Database,
    notify: Callable[[], None],
    provider: RelayOAuthProvider | None = None,
) -> FastMCP:
    """Build the connector. `notify` wakes the upload worker after a submission.

    The provider is shared with the OAuth routes mounted at the site root, so
    both halves read and write the same grants and tokens.
    """
    provider = provider or RelayOAuthProvider(database, settings.base_url)
    hosts = allowed_hosts(settings.base_url, settings.mcp_probe_allowed_hosts)
    server = FastMCP(
        name="Workout Relay",
        instructions=(
            "Send structured running plans to the person's Garmin Connect "
            "calendar. Fetch get_plan_format first: it carries the rules, the "
            "JSON Schema and a worked example. Always validate_plan before "
            "submit_plan, and repair every reported error. submit_plan queues "
            "the work and returns immediately; poll get_plan_status until it "
            "is completed or failed. Reuse a workout's id to edit or move it; "
            "a new id creates a separate workout."
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        # Verification only: the authorization endpoints live at the site
        # root, where a discovering client looks for them.
        token_verifier=ProviderTokenVerifier(provider),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.base_url),
            resource_server_url=AnyHttpUrl(f"{settings.base_url}/mcp/"),
            required_scopes=["plans:read"],
            # Our tokens are bound to an account and a client, and clients do
            # not all send a resource. Audience checking is a later hardening.
            validate_token_resource=False,
        ),
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts,
            allowed_origins=[f"https://{host}" for host in hosts]
            + [f"http://{host}" for host in hosts],
        ),
    )

    @server.tool(
        description=(
            "The plan format rules, endpoints and worked example, in English "
            "('en') or French ('fr'). Read this before writing a plan."
        )
    )
    def get_plan_format(language: str = "en") -> str:
        _actor("plans:read")
        return _ok(
            {
                "format": assistant_instructions(
                    "fr" if language == "fr" else "en", settings.base_url
                ),
                "schema": plan_schema(),
                "example": example_plan(),
            }
        )

    @server.tool(
        description=(
            "Check the plan against the schema without sending anything. "
            "Returns structured errors with a path and code for each problem."
        )
    )
    def validate_plan_tool(plan: dict[str, Any]) -> str:
        _actor("plans:read")
        try:
            check_plan(plan, settings.max_plan_bytes)
        except PlanRejected as rejected:
            return _error(rejected.code, errors=rejected.errors)
        return _ok({"plan_id": plan["plan_id"], "workout_count": len(plan["workouts"])})

    @server.tool(
        description=(
            "Queue a validated plan for delivery to Garmin. Returns a "
            "submission id immediately; the upload happens in the background, "
            "so poll get_plan_status until it is completed or failed. Reusing "
            "a workout id edits or moves that workout instead of duplicating."
        )
    )
    def submit_plan(plan: dict[str, Any]) -> str:
        user_id = _actor("plans:write")
        with database.session() as db:
            try:
                accepted: Accepted = queue_plan(
                    db, database, user_id, plan, settings.max_plan_bytes
                )
            except PlanRejected as rejected:
                return _error(rejected.code, errors=rejected.errors)
        notify()
        return _ok(accepted.as_dict())

    @server.tool(description="The outcome of one submission, by its id.")
    def get_plan_status(submission_id: str) -> str:
        user_id = _actor("plans:read")
        with database.session() as db:
            items = {item.id: item for item in recent_submissions(db, user_id, 50)}
            item = items.get(submission_id)
            if item is None:
                return _error("plan_not_found")
            return _ok({"submission": submission_json(item)})

    @server.tool(description="Recent plan submissions for this account, newest first.")
    def list_recent_plans(limit: int = 5) -> str:
        user_id = _actor("plans:read")
        with database.session() as db:
            items = recent_submissions(db, user_id, limit)
            return _ok({"items": [submission_json(item) for item in items]})

    @server.tool(
        description=(
            "Whether the account has a working Garmin connection. Check this "
            "before submitting; without it a plan cannot be delivered."
        )
    )
    def get_garmin_status() -> str:
        user_id = _actor("plans:read")
        with database.session() as db:
            connection = db.get(GarminConnection, user_id)
        if connection is None:
            return _ok({"connected": False, "status": "disconnected"})
        return _ok(
            {
                "connected": connection.status == "connected",
                "status": connection.status,
                "display_name": connection.display_name,
            }
        )

    return server
