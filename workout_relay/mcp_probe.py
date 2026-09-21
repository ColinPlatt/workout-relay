"""A thin MCP server that answers one question: do assistants receive files?

This is a deliberately minimal experiment, not the activity feature. It serves
fixed sample files and never touches Garmin, the database, or any user data, so
it can be exposed without an account behind it. It exists to find out whether
ChatGPT and Claude, on a phone, can actually read the contents of a FIT or TCX
file delivered over MCP - rather than only showing a link to one.

Both delivery shapes are offered, because clients differ in what they surface:

* a tool that returns the file as an embedded resource, and
* concrete resources the client can read directly.

Keep it disabled unless a test is running (``MCP_PROBE_ENABLED``).
"""

from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import BlobResourceContents, EmbeddedResource, TextResourceContents
from pydantic import AnyUrl
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .samples import SAMPLES, read_fit_header, sample_by_id, sample_file

logger = logging.getLogger(__name__)

FORMATS = {"fit": "application/vnd.ant.fit", "tcx": "application/vnd.garmin.tcx+xml"}


def activity_json(sample) -> dict:
    """The selection metadata the real endpoint will return."""
    return {
        "activity_id": sample.activity_id,
        "name": sample.name,
        "sport": sample.sport,
        "started_at": sample.started_at.isoformat(),
        "duration_sec": sample.duration_sec,
        "formats": sorted(FORMATS),
    }


def file_resource(sample, file_format: str, directory: Path | None):
    """Return the sample as MCP resource contents.

    TCX is XML and travels as text; FIT is binary and travels base64-encoded in
    a blob. If a client can only handle one of those, this is where it shows.
    """
    data = sample_file(sample, file_format, directory)
    uri = AnyUrl(f"activity://{sample.activity_id}.{file_format}")
    if file_format == "tcx":
        return TextResourceContents(uri=uri, mimeType=FORMATS[file_format], text=data.decode())
    return BlobResourceContents(
        uri=uri, mimeType=FORMATS[file_format], blob=base64.b64encode(data).decode()
    )


def allowed_hosts(base_url: str, extra: str = "") -> list[str]:
    """Hosts the probe will answer for.

    The SDK's DNS-rebinding protection rejects every Host header unless it is
    listed, so the deployment's own hostname has to be named here or every
    request comes back 421.
    """
    from urllib.parse import urlsplit

    netloc = urlsplit(base_url).netloc
    hosts = ["localhost", "127.0.0.1", "localhost:8000", "127.0.0.1:8000"]
    for candidate in [netloc, netloc.split(":")[0], *extra.split(",")]:
        candidate = candidate.strip()
        if candidate and candidate not in hosts:
            hosts.append(candidate)
    return hosts


def build_server(
    samples_dir: str | None = None,
    base_url: str = "http://localhost:8000",
    extra_hosts: str = "",
) -> FastMCP:
    directory = Path(samples_dir) if samples_dir else None
    hosts = allowed_hosts(base_url, extra_hosts)
    server = FastMCP(
        name="Workout Relay file delivery probe",
        instructions=(
            "Sample running activities for testing file delivery. Use "
            "list_recent_activities to choose one, then get_activity_file to "
            "fetch its FIT or TCX contents. The files are synthetic samples."
        ),
        streamable_http_path="/",
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(
            allowed_hosts=hosts,
            allowed_origins=[f"https://{host}" for host in hosts] + [f"http://{host}" for host in hosts],
        ),
    )

    @server.tool(description="List the sample activities available for download.")
    def list_recent_activities(limit: int = 5) -> str:
        chosen = list(SAMPLES)[: max(1, min(limit, 20))]
        return json.dumps({"items": [activity_json(sample) for sample in chosen]}, indent=2)

    @server.tool(
        description=(
            "Download one sample activity file. file_format is 'fit' (binary, "
            "returned base64-encoded) or 'tcx' (XML text). Report whether you "
            "can read the contents, and summarise what you find."
        )
    )
    def get_activity_file(activity_id: str, file_format: str = "fit") -> EmbeddedResource:
        sample = sample_by_id(activity_id)
        if sample is None:
            raise ValueError(f"unknown activity_id: {activity_id}")
        if file_format not in FORMATS:
            raise ValueError(f"file_format must be one of {sorted(FORMATS)}")
        return EmbeddedResource(
            type="resource", resource=file_resource(sample, file_format, directory)
        )

    @server.tool(
        description=(
            "Facts the server can prove about a sample file, for comparison "
            "with what the assistant reports reading."
        )
    )
    def describe_activity_file(activity_id: str, file_format: str = "fit") -> str:
        sample = sample_by_id(activity_id)
        if sample is None:
            raise ValueError(f"unknown activity_id: {activity_id}")
        data = sample_file(sample, file_format, directory)
        facts = {"bytes": len(data), "sha256_prefix": _digest(data)}
        if file_format == "fit":
            facts |= read_fit_header(data)
        facts["expected_track_points"] = sample.points
        return json.dumps(facts, indent=2)

    for sample in SAMPLES:
        for file_format in FORMATS:
            _register_resource(server, sample, file_format, directory)
    return server


def _digest(data: bytes) -> str:
    import hashlib

    return hashlib.sha256(data).hexdigest()[:16]


def _register_resource(server: FastMCP, sample, file_format: str, directory: Path | None) -> None:
    uri = f"activity://{sample.activity_id}.{file_format}"

    def read(sample=sample, file_format=file_format):
        # FunctionResource hands bytes to the client as a blob.
        return sample_file(sample, file_format, directory)

    server.add_resource(
        FunctionResource(
            uri=AnyUrl(uri),
            name=f"{sample.name} ({file_format.upper()})",
            description=f"Sample {file_format.upper()} activity file for delivery testing.",
            mime_type=FORMATS[file_format],
            fn=read,
        )
    )


class BearerGate:
    """Optional shared secret, for when a connector can send a header.

    Connector user interfaces often cannot, so this stays off by default. The
    probe only ever serves synthetic samples, which is what makes that safe.
    """

    def __init__(self, app: ASGIApp, token: str):
        self.app = app
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode().lower(): value.decode() for key, value in scope.get("headers", [])}
        if headers.get("authorization") != f"Bearer {self.token}":
            await JSONResponse({"code": "probe_unauthorized"}, status_code=401)(scope, receive, send)
            return
        await self.app(scope, receive, send)
