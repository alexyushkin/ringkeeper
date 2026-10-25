"""A secret value must never appear in a tool result, error, log line, audit row,
decision or CLI output, whatever path a call takes."""

import json
import logging
from collections.abc import Iterator
from datetime import timedelta
from typing import Any
from urllib.parse import quote

import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from typer.testing import CliRunner

from ringkeeper.adapters import supabase
from ringkeeper.cli.main import app
from ringkeeper.db import session_scope
from ringkeeper.tokens import issue_token
from tests.conftest import TARGET_PASSWORD
from tests.fake_n8n import API_KEY
from tests.helpers import table_text

pytestmark = pytest.mark.anyio

SECRETS = [TARGET_PASSWORD, quote(TARGET_PASSWORD, safe=""), API_KEY]


def assert_clean(where: str, value: str) -> None:
    for secret in SECRETS:
        assert secret not in value, f"secret leaked into {where}"


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    with caplog.at_level(logging.DEBUG):
        yield caplog


def mcp(url: str, service: str = "supabase") -> Client[Any]:
    bearer, _ = issue_token("acme-web", timedelta(minutes=10))
    headers = {"Authorization": f"Bearer {bearer}"}
    return Client(StreamableHttpTransport(f"{url}/mcp/{service}", headers=headers))


def result_text(result: Any) -> str:
    parts = [getattr(block, "text", "") for block in result.content]
    parts.append(json.dumps(result.structured_content, default=str))
    return "\n".join(parts)


async def test_secrets_never_escape(
    gateway: str, target_dsn: str, logs: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[str, str]] = []
    calls: list[tuple[str, dict[str, Any], str]] = [
        # A read whose result would contain the password.
        ("query", {"sql": "SELECT current_user, %s::text AS echo", "params": ["x"]}, "supabase"),
        # Errors quoting the target: unknown relation, bad syntax.
        ("query", {"sql": "SELECT * FROM missing_relation"}, "supabase"),
        ("query", {"sql": "SELEKT 1"}, "supabase"),
        # The secret smuggled into arguments: refused, and redacted in the audit row.
        ("query", {"sql": f"SELECT '{TARGET_PASSWORD}'"}, "supabase"),
        (
            "execute",
            {"sql": f"INSERT INTO notes (body) VALUES ('{TARGET_PASSWORD}')",  # noqa: S608
             "instance": "acme-supabase-dev"},
            "supabase",
        ),
        # Writes on dev (executed) and prod (held).
        ("execute", {"sql": "INSERT INTO notes (body) VALUES ('ok')", "instance": "acme-supabase-dev"}, "supabase"),  # noqa: E501
        ("execute", {"sql": "UPDATE notes SET body = 'held'", "instance": "acme-supabase-prod"}, "supabase"),  # noqa: E501
        # n8n with its API key; the host doesn't exist, so the call fails.
        ("list_workflows", {"instance": "acme-n8n"}, "n8n"),
    ]  # fmt: skip
    for tool, args, service in calls:
        async with mcp(gateway, service) as client:
            result = await client.call_tool(tool, args, raise_on_error=False)
        seen.append((f"{tool} result", result_text(result)))

    # An unexpected exception inside an adapter: logged with a traceback.
    async def explode(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RuntimeError(f"driver blew up connecting with {target_dsn}")

    monkeypatch.setattr(supabase, "_run", explode)  # every supabase tool goes through it
    async with mcp(gateway) as client:
        result = await client.call_tool("list_tables", {}, raise_on_error=False)
    seen.append(("unexpected error result", result_text(result)))

    runner = CliRunner()
    held_ids = [
        line.split()[0] for line in runner.invoke(app, ["decision", "list"]).stdout.splitlines()[1:]
    ]
    assert held_ids, "the prod write should have been held"
    for args in (
        ["decision", "show", held_ids[0]],
        ["decision", "approve", held_ids[0]],
        ["secret", "check", "acme-supabase-dev"],
        ["audit", "list", "--limit", "1000"],
    ):
        cli_result = runner.invoke(app, args)
        seen.append((f"cli {' '.join(args[:2])}", cli_result.output))

    with session_scope() as session:
        for table in ("audit_events", "decisions"):
            seen.extend((f"{table} row", row) for row in table_text(session, table))

    seen.append(("logs", logs.text))
    seen.extend(("log record", r.getMessage()) for r in logs.records)

    for where, value in seen:
        assert_clean(where, value)

    smuggled = [v for w, v in seen if w == "query result" and "secret value" in v]
    assert smuggled, "a call carrying a secret in its args should be refused"
    assert any("audit_events" in w and "[redacted]" in v for w, v in seen)
