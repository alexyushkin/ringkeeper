"""A client's connection reaches only that client's projects: when it's linked, by any route,
and again on every call, so a link that got into the database some other way is still refused."""

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Instance,
    InstanceType,
    Project,
    ProjectInstance,
)
from ringkeeper.schemas import BindingIn
from ringkeeper.services import bindings
from ringkeeper.services.calls import RefusalError, bound_instances, run_call
from ringkeeper.vault import get_vault
from tests.helpers import bind, make_instance, make_project
from tests.test_registry import apply, instance, project, write

RULE = "A client's connection can only be used by that client's projects."
CALLER = Caller("acme-web", "session-1", "token-1")


def test_a_registry_file_cant_link_across_clients(database_url: str, tmp_path: Path) -> None:
    data = {
        "instances": [instance("globex-db", owner="globex")],
        "projects": [project("acme-web", {"instance": "globex-db"})],
    }
    error = apply(write(tmp_path, data), exit_code=1)
    assert "binding acme-web -> globex-db" in error
    assert RULE in error
    with session_scope() as session:  # nothing from the file was kept
        assert session.scalars(select(ProjectInstance)).all() == []


def test_the_cli_and_every_other_route_are_refused_the_same_way(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
        make_instance(session, "globex-db", owner="globex")
        with pytest.raises(RuleViolationError, match=RULE):
            bindings.set_binding(session, BindingIn(project="acme-web", instance="globex-db"))


def test_a_shared_tool_still_goes_to_any_client(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
        make_project(session, "globex-portal", owner="globex")
        make_instance(session, "browser", type=InstanceType.MCP, shared=True, owner="you")
    bind("acme-web", "browser")
    bind("globex-portal", "browser")


def _force_link(project_name: str, instance_name: str) -> None:
    """As a hand edit of the database would, past every check."""
    with session_scope() as session:
        found = session.scalars(select(Project).where(Project.name == project_name)).one()
        other = session.scalars(select(Instance).where(Instance.name == instance_name)).one()
        session.add(ProjectInstance(project=found, instance=other, is_default=True))


def test_a_link_forced_into_the_database_is_refused_at_call_time(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
        make_instance(session, "globex-db", owner="globex", secret_ref="file://globex/db")
    _force_link("acme-web", "globex-db")
    get_vault().store("file://globex/db", "postgresql://never-reached.example/db")

    for named in ("globex-db", None):  # by name, and as the read default it was forced to be
        call = ToolCall(CALLER, InstanceType.SUPABASE, "list_tables", named, {})
        with pytest.raises(RefusalError) as refused:
            asyncio.run(run_call(call, get_vault()))
        # Said to the agent the same way as a connection that doesn't exist.
        assert "globex-db" not in str(refused.value).split("can use:")[-1]

    with session_scope() as session:
        rows = session.scalars(select(AuditEvent).where(AuditEvent.project == "acme-web")).all()
        reasons = {(r.outcome, r.reason) for r in rows}
    assert (
        AuditOutcome.REFUSED,
        "instance 'globex-db' belongs to client 'globex', project 'acme-web' to 'acme'",
    ) in reasons
    assert AuditOutcome.EXECUTED not in {outcome for outcome, _ in reasons}
    # And the worker isn't shown it.
    assert [i["name"] for i in bound_instances(CALLER)] == []
