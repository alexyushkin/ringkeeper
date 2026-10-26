"""One client's connection shared by its projects, each link narrowed differently.

The connection stands in for Neon's remote MCP server (tests/fixtures/
fake_remote_server.py answers with the IDs it was called with). acme-web is
limited to project p-alpha and branch br-1, acme-app to project p-beta, and
acme-ops has the whole connection. The gateway fills in, checks or refuses
IDs by argument name, and keeps account-wide tools from the narrowed links.
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from ringkeeper import remote
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Decision, Env, InstanceType
from ringkeeper.services import bindings, decisions, projects
from ringkeeper.services.calls import RefusalError, approve, run_call
from ringkeeper.vault import get_vault
from tests.helpers import bind, make_instance, make_project

FAKE = Path(__file__).parent / "fixtures" / "fake_remote_server.py"
ALPHA = {"project": "p-alpha", "branch": "br-1"}


@pytest.fixture
def shared(database_url: str) -> None:
    get_vault().store(
        "file://acme/neon", json.dumps({"command": sys.executable, "args": [str(FAKE)]})
    )
    with session_scope() as session:
        for name in ("acme-web", "acme-app", "acme-ops"):
            make_project(session, name)
        make_project(session, "globex-portal", owner="globex")
        make_instance(
            session, "acme-neon", type=InstanceType.MCP, env=Env.DEV,
            secret_ref="file://acme/neon", config={"remote": "neon"},
        )  # fmt: skip
    bind("acme-web", "acme-neon", scope=ALPHA)
    bind("acme-app", "acme-neon", scope={"project": "p-beta"})
    bind("acme-ops", "acme-neon")
    listing("acme-ops")  # the gateway learns each tool's arguments from the server


def call(project: str, tool: str, **arguments: Any) -> dict[str, Any]:
    caller = Caller(project, "session-1", "token-1")
    args = {"tool": tool, "arguments": arguments}
    return asyncio.run(
        run_call(ToolCall(caller, InstanceType.MCP, "call_tool", "acme-neon", args), get_vault())
    )


def listing(project: str) -> dict[str, Any]:
    caller = Caller(project, "session-1", "token-1")
    return asyncio.run(
        run_call(ToolCall(caller, InstanceType.MCP, "list_tools", "acme-neon", {}), get_vault())
    )


@pytest.mark.usefixtures("shared")
def test_each_project_gets_its_own_ids_filled_in() -> None:
    assert call("acme-web", "describe_project")["structured"] == {"project_id": "p-alpha"}
    assert call("acme-app", "describe_project")["structured"] == {"project_id": "p-beta"}
    assert call("acme-ops", "describe_project")["structured"] == {"project_id": None}
    branch = call("acme-web", "describe_branch")["structured"]
    assert branch == {"project_id": "p-alpha", "branch_id": "br-1"}
    assert call("acme-app", "describe_branch")["structured"] == {
        "project_id": "p-beta", "branch_id": None,
    }  # fmt: skip


@pytest.mark.usefixtures("shared")
def test_another_project_s_ids_are_refused() -> None:
    with pytest.raises(RefusalError, match="limited to neon project 'p-alpha'"):
        call("acme-web", "describe_project", project_id="p-beta")
    with pytest.raises(RefusalError, match="limited to branch 'br-1'"):
        call("acme-web", "describe_branch", project_id="p-alpha", branch_id="br-2")
    # The project's own IDs, given explicitly, are fine.
    assert call("acme-app", "describe_project", project_id="p-beta")["structured"] == {
        "project_id": "p-beta"
    }


@pytest.mark.usefixtures("shared")
def test_account_wide_tools_stay_off_a_narrowed_link() -> None:
    with pytest.raises(RefusalError, match="whole Neon account"):
        call("acme-web", "list_projects")
    assert call("acme-ops", "list_projects")["structured"] == {"result": ["p-alpha", "p-beta"]}
    assert call("acme-web", "search_docs", query="branches")["text"] == "docs about branches"
    names = {t["name"] for t in listing("acme-web")["tools"]}
    assert "list_projects" not in names
    assert {"describe_project", "run_sql", "search_docs"} <= names
    assert listing("acme-web")["limited_to"] == ALPHA
    assert "limited_to" not in listing("acme-ops")


@pytest.mark.usefixtures("shared")
def test_sql_is_held_with_the_ids_pinned_and_checked_by_its_statement() -> None:
    insert = call("acme-web", "run_sql", sql="INSERT INTO notes (body) VALUES ('x')")
    delete = call("acme-web", "run_sql", sql="DELETE FROM notes")
    with session_scope() as session:
        added = decisions.get_decision(session, insert["decision_id"])
        removed = decisions.get_decision(session, delete["decision_id"])
        assert added.args is not None
        assert added.args["arguments"] == {
            "sql": "INSERT INTO notes (body) VALUES ('x')", "project_id": "p-alpha",
            "branch_id": "br-1",
        }  # fmt: skip
        assert decisions.confirmation_needed(added) is None  # SQL decides, not its hints
        assert decisions.confirmation_needed(removed) == "deletes rows"


@pytest.mark.usefixtures("shared")
def test_a_held_call_outside_a_changed_scope_is_not_run() -> None:
    held = call("acme-web", "run_sql", sql="INSERT INTO notes (body) VALUES ('x')")
    bind("acme-web", "acme-neon", scope={"project": "p-gamma"})
    with session_scope() as session:
        decision = decisions.get_decision(session, held["decision_id"])
        decision.answer = {"choice": "approve", "by": "owner"}
    finished: Decision = asyncio.run(approve(held["decision_id"], by="owner", vault=get_vault()))
    assert finished.error is not None
    assert "outside the link's scope" in finished.error


@pytest.mark.usefixtures("shared")
def test_a_connection_is_shared_only_among_one_client_s_projects() -> None:
    with session_scope() as session:
        with pytest.raises(RuleViolationError, match="one client's projects"):
            bindings.check_same_client(session, "globex-portal", "acme-neon")
        bindings.check_same_client(session, "acme-ops", "acme-neon")


def test_what_a_link_may_be_narrowed_by() -> None:
    neon = remote.spec_for("mcp", {"remote": "neon"})
    assert neon is not None
    assert remote.check_scope(neon, {"project": " p-1 ", "branch": ""}) == {"project": "p-1"}
    with pytest.raises(remote.ScopeError, match="before the Branch"):
        remote.check_scope(neon, {"branch": "br-1"})
    with pytest.raises(remote.ScopeError, match="can't be narrowed by region"):
        remote.check_scope(neon, {"region": "eu"})
    assert remote.spec_for("mcp", {"remote": "notion"}) is None  # no IDs to pin
    assert remote.spec_for("mcp", {}) is None
    airtable = remote.spec_for("airtable", {})
    assert airtable is not None
    assert remote.narrow(airtable, {"base": "appA"}, "list_tables", {}, ["base_id"]) == {
        "base_id": "appA"
    }
    with pytest.raises(remote.ScopeError, match="list_tools first"):
        remote.narrow(neon, {"project": "p"}, "describe_project", {}, None)


@pytest.mark.usefixtures("shared")
def test_changing_a_link_keeps_its_scope_unless_given() -> None:
    bind("acme-web", "acme-neon", is_default=True)  # the default toggle sends no scope
    with session_scope() as session:
        [link] = list(projects.get_project(session, "acme-web").bindings)
        assert (link.is_default, link.scope) == (True, ALPHA)
    bind("acme-web", "acme-neon", scope={})  # an empty scope: the whole connection again
    assert call("acme-web", "describe_project")["structured"] == {"project_id": None}
