"""End to end: a real MCP client over Streamable HTTP against the running gateway."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from sqlalchemy import select
from typer.testing import CliRunner

from ringkeeper.cli.main import app
from ringkeeper.db import session_scope
from ringkeeper.gateway.app import create_app
from ringkeeper.keys import MasterKeyError
from ringkeeper.models import AuditEvent, AuditOutcome
from ringkeeper.services import revocations
from ringkeeper.tokens import issue_token, verify_token

pytestmark = pytest.mark.anyio


def token(project: str = "acme-web", **kwargs: Any) -> str:
    value, _ = issue_token(project, timedelta(minutes=10), **kwargs)
    return value


def client(url: str, bearer: str, service: str = "supabase") -> Client[Any]:
    headers = {"Authorization": f"Bearer {bearer}"}
    return Client(StreamableHttpTransport(f"{url}/mcp/{service}", headers=headers))


def text_of(result: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


def audit_rows() -> list[AuditEvent]:
    with session_scope() as session:
        return list(session.scalars(select(AuditEvent).order_by(AuditEvent.id)).all())


async def test_health(gateway: str) -> None:
    async with httpx2.AsyncClient() as http:
        response = await http.get(f"{gateway}/health")
    assert response.json() == {"status": "ok", "services": ["n8n", "supabase"]}


async def test_every_tool_takes_instance_and_declares_its_kind(gateway: str) -> None:
    async with client(gateway, token()) as mcp:
        tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {"list_tables", "describe_table", "query", "execute"}
    for tool in tools.values():
        assert "instance" in tool.input_schema["properties"]
        assert "instance" not in tool.input_schema.get("required", [])
        assert "target" not in tool.input_schema["properties"]
    assert tools["query"].annotations is not None
    assert tools["query"].annotations.read_only_hint is True
    assert tools["execute"].annotations is not None
    assert tools["execute"].annotations.destructive_hint is True


async def test_read_on_own_instance_works(gateway: str) -> None:
    async with client(gateway, token()) as mcp:
        result = await mcp.call_tool("query", {"sql": "SELECT body FROM notes"})
    assert result.structured_content is not None
    assert result.structured_content["rows"] == [["hello"]]


async def test_other_clients_instance_is_refused(gateway: str) -> None:
    async with client(gateway, token()) as mcp:
        result = await mcp.call_tool(
            "query", {"sql": "SELECT 1", "instance": "globex-supabase-dev"}, raise_on_error=False
        )
    assert result.is_error
    assert "instance 'globex-supabase-dev' is not available to project 'acme-web'" in text_of(
        result
    )


async def test_prod_write_is_held(gateway: str) -> None:
    async with client(gateway, token()) as mcp:
        result = await mcp.call_tool(
            "execute",
            {"sql": "INSERT INTO notes (body) VALUES ('x')", "instance": "acme-supabase-prod"},
        )
    assert result.structured_content is not None
    assert result.structured_content["status"] == "held_for_approval"
    assert "Held for approval" in result.structured_content["message"]


async def http_post(url: str, headers: dict[str, str]) -> httpx2.Response:
    body: dict[str, object] = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    headers = {**headers, "Accept": "application/json, text/event-stream"}
    async with httpx2.AsyncClient() as http:
        return await http.post(f"{url}/mcp/supabase", json=body, headers=headers)


@pytest.mark.parametrize(
    ("headers", "reason"),
    [
        ({}, "a bearer worker token is required"),
        ({"Authorization": "Basic abc"}, "a bearer worker token is required"),
        ({"Authorization": "Bearer not-a-token"}, "malformed"),
    ],
)
async def test_bad_or_missing_token_gets_401_with_reason(
    gateway: str, headers: dict[str, str], reason: str
) -> None:
    response = await http_post(gateway, headers)
    assert response.status_code == 401
    assert reason in response.json()["error_description"]
    assert reason in response.headers["WWW-Authenticate"]
    event = audit_rows()[-1]
    assert (event.outcome, event.project) == (AuditOutcome.REFUSED, None)


async def test_expired_token_is_refused(gateway: str) -> None:
    expired, _ = issue_token(
        "acme-web", timedelta(minutes=5), now=datetime.now(UTC) - timedelta(hours=1)
    )
    response = await http_post(gateway, {"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert "expired" in response.json()["error_description"]


async def test_tampered_token_is_refused(gateway: str) -> None:
    header, payload, signature = token().split(".")
    forged = f"{header}.{payload[:-2]}AA.{signature}"
    response = await http_post(gateway, {"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


async def test_token_project_decides_what_is_reachable(gateway: str) -> None:
    async with client(gateway, token("globex-portal")) as mcp:
        own = await mcp.call_tool("query", {"sql": "SELECT 1 AS one"})
        other = await mcp.call_tool(
            "query", {"sql": "SELECT 1", "instance": "acme-supabase-dev"}, raise_on_error=False
        )
    assert own.structured_content is not None
    assert own.structured_content["rows"] == [[1]]
    assert other.is_error
    assert "not available to project 'globex-portal'" in text_of(other)


async def test_n8n_endpoint_is_separate(gateway: str) -> None:
    async with client(gateway, token(), service="n8n") as mcp:
        names = {t.name for t in await mcp.list_tools()}
    assert "update_workflow" in names
    assert "query" not in names


@pytest.mark.usefixtures("data_dir")
def test_gateway_refuses_to_start_without_a_master_key() -> None:
    with pytest.raises(MasterKeyError, match="no master key"):
        create_app()


async def test_revoked_token_is_refused_and_audited(gateway: str) -> None:

    bearer = token()
    jti = verify_token(bearer).token_id
    async with client(gateway, bearer) as mcp:
        assert await mcp.list_tools()  # works before

    result = CliRunner().invoke(app, ["token", "revoke", jti, "--reason", "test"])
    assert result.exit_code == 0, result.output
    assert f"revoked {jti}" in result.stdout
    again = CliRunner().invoke(app, ["token", "revoke", jti])
    assert "already revoked" in again.stdout

    response = await http_post(gateway, {"Authorization": f"Bearer {bearer}"})
    assert response.status_code == 401
    assert response.json()["error_description"] == "worker token has been revoked"
    event = audit_rows()[-1]
    assert (event.outcome, event.project, event.token_id) == (
        AuditOutcome.REFUSED,
        "acme-web",
        jti,
    )
    assert event.reason == "worker token has been revoked"


async def test_other_tokens_still_work_after_a_revocation(gateway: str) -> None:
    with session_scope() as db:
        revocations.revoke(db, "some-other-jti", reason="test", by="test")
    async with client(gateway, token()) as mcp:
        assert await mcp.list_tools()
