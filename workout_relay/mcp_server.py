"""The assistant connector: MCP tools over an authorized Workout Relay account.

Every tool acts for the account that granted the connection, identified by the
access token's subject. Garmin credentials are never reachable from here: the
connector can queue a plan or read completed-activity metrics with separate
consent. Garmin tokens always stay server-side.

Results are plain JSON. Assistants parse that reliably, which a delivery
experiment confirmed on both ChatGPT and Claude, whereas binary attachments
did not survive on every client.
"""

from __future__ import annotations

import asyncio
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
from .activities import ActivityError, ActivityService
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
    activities: ActivityService | None = None,
    tokens_store=None,
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
            "a new id creates a separate workout. For completed activities, "
            "use list_activities then get_activity with an id from that list. "
            "These require separately consented activities:read access; reconnect "
            "to grant it. Metrics have explicit units; null means unavailable, not zero."
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
            required_scopes=[],  # Authentication here; authorization per tool.
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
        live = tokens_store.live(connection) if tokens_store else connection.status == "connected"
        retention = connection.retention or "persistent"
        status = connection.status
        if retention == "visit" and connection.status == "connected" and not live:
            # The person allowed Garmin access for one visit, and it has ended.
            status = "visit_expired"
        payload = {
            "connected": live,
            "status": status,
            "display_name": connection.display_name,
            "retention": retention,
        }
        if retention == "visit":
            payload["note"] = (
                "This account keeps its Garmin session for one visit only. "
                "Plans can be delivered only while that window is open; if a "
                "submission fails, ask the person to reconnect Garmin."
            )
        return _ok(payload)

    async def read_activity(detail: str | None = None, **query) -> str:
        try:
            user_id = _actor("activities:read")
        except NotAuthorized:
            return _error("scope_required", required_scope="activities:read",
                          message="Reconnect and approve activity access.")
        if activities is None:
            return _error("activities_unavailable")
        try:
            # The service holds ownership inside the thread throughout the read
            # and refreshed-token persistence, including caller cancellation.
            payload = await asyncio.to_thread(
                activities.get if detail is not None else activities.list,
                user_id, **({"activity_id": detail, **query} if detail is not None else query),
            )
            return _ok(payload)
        except ActivityError as exc:
            return _error(exc.code)

    @server.tool(description=(
        "List completed Garmin activities (not planned workouts). Requires activities:read. "
        "Returns metrics with explicit units, no GPS. limit is 1–50; use next_start "
        "for older pages, null means no next page. Optional sport: running."
    ))
    async def list_activities(limit: int = 5, start: int = 0, sport: str | None = None) -> str:
        return await read_activity(limit=limit, start=start, sport=sport)

    @server.tool(description=(
        "Read a completed activity: whole-activity metrics, its laps, and "
        "optionally the sample-by-sample series (heart rate, pace, elevation, "
        "cadence, power, temperature over time) that a FIT file would carry. "
        "Set samples to the number of points wanted, up to 1000; omit it for "
        "summary and laps only, which is much smaller. Requires "
        "activities:read and an id from this account's list_activities within "
        "the last 24 hours. Missing metrics are null, never zero. Location is "
        "never included and no files are served."
    ))
    async def get_activity(activity_id: str, samples: int | None = None, laps: bool = True) -> str:
        return await read_activity(detail=activity_id, samples=samples, laps=laps)

    return server
