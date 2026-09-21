import base64
import io
import json
from dataclasses import replace

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import BlobResourceContents, EmbeddedResource, TextResourceContents

from workout_relay.app import create_app
from workout_relay.mcp_probe import build_server
from workout_relay.samples import SAMPLES, fit_bytes, read_fit_header, tcx_text

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "probe-test", "version": "0"},
    },
}
MCP_HEADERS = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}


@pytest.fixture
def probe_settings(settings):
    return replace(settings, mcp_probe_enabled=True)


async def connected(server):
    return create_connected_server_and_client_session(server._mcp_server)


@pytest.mark.anyio
async def test_tools_and_resources_are_offered(probe_settings):
    async with await connected(build_server()) as client:
        tools = {tool.name for tool in (await client.list_tools()).tools}
        assert tools == {"list_recent_activities", "get_activity_file", "describe_activity_file"}
        resources = {str(item.uri) for item in (await client.list_resources()).resources}
        assert resources == {
            f"activity://{sample.activity_id}.{extension}"
            for sample in SAMPLES
            for extension in ("fit", "tcx")
        }
        listed = json.loads((await client.call_tool("list_recent_activities", {})).content[0].text)
        assert [item["activity_id"] for item in listed["items"]] == [s.activity_id for s in SAMPLES]
        assert listed["items"][0]["formats"] == ["fit", "tcx"]


@pytest.mark.anyio
async def test_fit_arrives_as_a_decodable_blob():
    sample = SAMPLES[0]
    async with await connected(build_server()) as client:
        result = await client.call_tool(
            "get_activity_file", {"activity_id": sample.activity_id, "file_format": "fit"}
        )
    block = result.content[0]
    assert isinstance(block, EmbeddedResource)
    assert isinstance(block.resource, BlobResourceContents)
    assert block.resource.mimeType == "application/vnd.ant.fit"
    delivered = base64.b64decode(block.resource.blob)
    assert delivered == fit_bytes(sample)
    # Whatever the client does with it, the bytes that left are a valid FIT file.
    assert read_fit_header(delivered)["data_size"] == len(delivered) - 16


@pytest.mark.anyio
async def test_tcx_arrives_as_readable_text():
    sample = SAMPLES[1]
    async with await connected(build_server()) as client:
        result = await client.call_tool(
            "get_activity_file", {"activity_id": sample.activity_id, "file_format": "tcx"}
        )
    resource = result.content[0].resource
    assert isinstance(resource, TextResourceContents)
    assert resource.text == tcx_text(sample)
    assert resource.text.count("<Trackpoint>") == sample.points


@pytest.mark.anyio
async def test_resource_read_delivers_the_same_bytes():
    sample = SAMPLES[0]
    async with await connected(build_server()) as client:
        contents = (await client.read_resource(f"activity://{sample.activity_id}.fit")).contents[0]
    assert base64.b64decode(contents.blob) == fit_bytes(sample)


@pytest.mark.anyio
async def test_description_matches_what_was_delivered():
    sample = SAMPLES[0]
    async with await connected(build_server()) as client:
        facts = json.loads(
            (
                await client.call_tool(
                    "describe_activity_file",
                    {"activity_id": sample.activity_id, "file_format": "fit"},
                )
            ).content[0].text
        )
    assert facts["bytes"] == len(fit_bytes(sample))
    assert facts["expected_track_points"] == sample.points


@pytest.mark.anyio
async def test_unknown_activity_and_format_are_refused():
    async with await connected(build_server()) as client:
        missing = await client.call_tool("get_activity_file", {"activity_id": "nope"})
        assert missing.isError
        bad_format = await client.call_tool(
            "get_activity_file",
            {"activity_id": SAMPLES[0].activity_id, "file_format": "gpx"},
        )
        assert bad_format.isError


@pytest.mark.anyio
async def test_real_exports_override_the_synthetic_samples(tmp_path):
    sample = SAMPLES[0]
    (tmp_path / f"{sample.activity_id}.fit").write_bytes(b"real export bytes")
    async with await connected(build_server(str(tmp_path))) as client:
        result = await client.call_tool(
            "get_activity_file", {"activity_id": sample.activity_id, "file_format": "fit"}
        )
    assert base64.b64decode(result.content[0].resource.blob) == b"real export bytes"


@pytest.mark.anyio
async def test_probe_is_absent_unless_enabled(settings):
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/mcp/", json=INITIALIZE, headers=MCP_HEADERS)
            assert response.status_code == 404


@pytest.mark.anyio
async def test_mounted_probe_completes_a_handshake(probe_settings):
    app = create_app(probe_settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/mcp/", json=INITIALIZE, headers=MCP_HEADERS)
            assert response.status_code == 200, response.text
            body = response.json()
            assert body["result"]["serverInfo"]["name"].startswith("Workout Relay")


@pytest.mark.anyio
async def test_shared_secret_is_enforced_when_configured(probe_settings):
    app = create_app(replace(probe_settings, mcp_probe_token="probe-secret"))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            denied = await client.post("/mcp/", json=INITIALIZE, headers=MCP_HEADERS)
            assert denied.status_code == 401
            allowed = await client.post(
                "/mcp/",
                json=INITIALIZE,
                headers=MCP_HEADERS | {"Authorization": "Bearer probe-secret"},
            )
            assert allowed.status_code == 200


def test_samples_are_valid_fit_files():
    fitdecode = pytest.importorskip("fitdecode")
    for sample in SAMPLES:
        messages = []
        with fitdecode.FitReader(io.BytesIO(fit_bytes(sample))) as reader:
            for frame in reader:
                if isinstance(frame, fitdecode.FitDataMessage):
                    messages.append(frame.name)
        assert messages.count("record") == sample.points
        assert "file_id" in messages and "session" in messages
