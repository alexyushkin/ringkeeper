"""Every one of Ringkeeper's own gateway tools: a wrong argument name is refused with what the
tool does take (each argument, its type, whether it's needed), and the name a model would
guess for an argument is taken too."""

import functools
from typing import Any

import anyio
import pytest
from fastmcp import Client, FastMCP
from fastmcp.tools import Tool

from ringkeeper.adapters import ADAPTERS
from ringkeeper.calls import Caller
from ringkeeper.gateway import memory as memory_server
from ringkeeper.gateway.app import build_server
from ringkeeper.gateway.architect import build_architect_server
from ringkeeper.gateway.arguments import renamed, takes
from ringkeeper.gateway.memory import build_memory_server
from ringkeeper.gateway.pass_server import build_pass_server

WRONG = "zz_not_an_argument"


def nobody() -> Caller:
    return Caller("hb-site", "s1", "t1")


@functools.cache
def servers() -> dict[str, FastMCP]:
    built: dict[str, FastMCP] = {
        name: build_server(adapter, None)  # pyright: ignore[reportArgumentType]
        for name, adapter in ADAPTERS.items()
    }
    return built | {
        "memory": build_memory_server(nobody),
        "pass": build_pass_server(nobody),
        "architect": build_architect_server(nobody),
    }


def every_tool() -> list[tuple[str, str]]:
    async def listed() -> list[tuple[str, str]]:
        found: list[tuple[str, str]] = []
        for name, server in servers().items():
            found += [(name, tool.name) for tool in await server.list_tools()]
        return found

    return anyio.run(listed)


def tool(server: str, name: str) -> Tool:
    async def found() -> Tool:
        made = await servers()[server].get_tool(name)
        assert made is not None
        return made

    return anyio.run(found)


def refused(server: str, name: str, arguments: dict[str, Any]) -> str:
    async def call() -> str:
        async with Client(servers()[server]) as client:
            result = await client.call_tool(name, arguments, raise_on_error=False)
        assert result.is_error, (server, name)
        return "\n".join(getattr(block, "text", "") for block in result.content)

    return anyio.run(call)


@pytest.mark.parametrize(("server", "name"), every_tool())
def test_a_wrong_name_is_told_what_the_tool_takes(server: str, name: str) -> None:
    said = refused(server, name, {WRONG: "a-value-not-to-repeat"})
    schema = tool(server, name).parameters
    assert f"`{WRONG}` isn't something this tool takes" in said
    assert f"It takes: {takes(schema)}." in said
    arguments: dict[str, Any] = schema.get("properties") or {}
    needed: list[str] = schema.get("required") or []
    for argument in arguments:
        assert f"{argument} (" in said
    for argument in needed:
        assert f"{argument} (" in said
        assert ", needed)" in said
    assert "a-value-not-to-repeat" not in said


def test_what_it_takes_reads_plainly() -> None:
    sql = takes(tool("supabase", "query").parameters)
    assert sql.startswith("sql (text, needed), params (")
    assert "max_rows (whole number" in sql
    assert takes(tool("memory", "recall").parameters) == "query (text)"
    assert takes(tool("supabase", "list_instances").parameters) == (
        "nothing: call it with no arguments"
    )
    assert "kind (one of Where things live, How we do it, Gotcha, Decision" in takes(
        tool("memory", "remember").parameters
    )


@pytest.mark.parametrize(
    ("server", "name", "written", "meant"),
    [
        # recall's own name is `query`; `words` (its old name), q, search and text are taken.
        *(("memory", "recall", {said: "courier"}, {"query": "courier"})
          for said in ("words", "q", "search", "text", "query")),
        *(("memory", "remember", {said: "Courier on Tuesdays"}, {"note": "Courier on Tuesdays"})
          for said in ("text", "content", "note")),
        ("supabase", "query", {"query": "select 1", "limit": 5},
         {"sql": "select 1", "max_rows": 5}),
        ("supabase", "execute", {"query": "delete from t"}, {"sql": "delete from t"}),
        ("oracle", "query", {"query": "select 1", "max": 3}, {"sql": "select 1", "max_rows": 3}),
        ("mcp", "list_tools", {"query": "invoice"}, {"words": "invoice"}),
        ("mailbox", "search_messages", {"query": "invoice"}, {"text": "invoice"}),
        ("http", "list_endpoints", {"query": "orders"}, {"search": "orders"}),
        ("vercel", "list_projects", {"query": "shop", "max_results": 2},
         {"search": "shop", "limit": 2}),
        ("airtable", "list_records", {"base_id": "b", "table": "t", "limit": 10},
         {"base_id": "b", "table": "t", "page_size": 10}),
        ("airtable", "get_record", {"base_id": "b", "table": "t", "id": "rec1"},
         {"base_id": "b", "table": "t", "record_id": "rec1"}),
        ("n8n", "get_workflow", {"id": "w1"}, {"workflow_id": "w1"}),
        ("twilio", "get_message", {"id": "SM1"}, {"message_sid": "SM1"}),
        ("resend", "get_email", {"id": "e1"}, {"email_id": "e1"}),
        ("mailbox", "get_message", {"id": "42"}, {"uid": "42"}),
        ("google", "read_doc", {"id": "d1"}, {"document_id": "d1"}),
        ("pass", "task_status", {"id": "t1"}, {"task_id": "t1"}),
        ("google", "create_doc", {"name": "Spring sale"}, {"title": "Spring sale"}),
        ("gmail", "search_messages", {"q": "invoice", "max_results": 5},
         {"query": "invoice", "limit": 5}),
    ],
)  # fmt: skip
def test_the_name_a_model_would_guess_is_taken(
    server: str, name: str, written: dict[str, Any], meant: dict[str, Any]
) -> None:
    assert renamed(tool(server, name).parameters, written) == meant


@pytest.mark.parametrize(
    ("server", "name", "written"),
    [
        ("memory", "recall", {"query": "a", "words": "b"}),  # its own name given: kept as sent
        ("google", "create_doc", {"title": "a", "text": "b"}),  # `text` is its own argument
        ("github", "get_issue", {"repo": "r", "id": 5}),  # an issue's number isn't its id
        ("google", "read_attachment", {"id": "x"}),  # two ids: which one is meant isn't clear
    ],
)
def test_a_name_the_tool_takes_itself_is_never_moved(
    server: str, name: str, written: dict[str, Any]
) -> None:
    assert renamed(tool(server, name).parameters, written) == written


def test_recall_by_its_old_name_reaches_the_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[str | None] = []

    def recall(_caller: Caller, words: str | None = None) -> dict[str, Any]:
        asked.append(words)
        return {"notes": []}

    monkeypatch.setattr(memory_server.memory, "recall", recall)

    async def call(arguments: dict[str, Any]) -> bool:
        async with Client(build_memory_server(nobody)) as client:
            result = await client.call_tool("recall", arguments, raise_on_error=False)
        return result.is_error

    for written in ({"query": "courier"}, {"words": "courier"}, {"q": "courier"}):
        assert anyio.run(call, written) is False
    assert asked == ["courier", "courier", "courier"]
