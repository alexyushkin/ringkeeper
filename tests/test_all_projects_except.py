"""A shared tool for all projects except some clients and projects.

Left out means not linked: the gateway refuses the call, and the agent's own
list of connections doesn't have it. A left-out client's new projects are left
out too; giving one back links its projects again.
"""

import pytest
from fastapi.testclient import TestClient

from ringkeeper.adapters import ADAPTERS
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import instances
from ringkeeper.services.calls import RefusalError, authorize, bound_instances
from tests import test_web_api
from tests.helpers import bind, make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

N8N = InstanceType.N8N


def can_use(project: str) -> bool:
    """Whether the gateway lets `project` call the shared tool, and the agent sees it."""
    listed = any(i["name"] == "tool" for i in bound_instances(Caller(project, "s", "t")))
    call = ToolCall(Caller(project, "s", "t"), N8N, "list_workflows", "tool", {})
    with session_scope() as session:
        try:
            authorize(session, call, ADAPTERS[N8N].tool("list_workflows"))
            allowed = True
        except RefusalError:
            allowed = False
    assert listed == allowed, f"{project}: listed {listed}, gateway allowed {allowed}"
    return allowed


def leave_out(*, clients: list[str] | None = None, projects: list[str] | None = None) -> None:
    with session_scope() as session:
        tool = instances.get_instance(session, "tool")
        data = InstanceIn.model_validate(tool, from_attributes=True).model_copy(
            update={"except_clients": clients or [], "except_projects": projects or []}
        )
        instances.update_instance(session, data)


@pytest.fixture
def everywhere(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="Acme")
        make_project(session, "acme-app", owner="Acme")
        make_project(session, "globex-portal", owner="globex")
        make_project(session, "seaside-messages", owner="Seaside")
        make_instance(
            session, "tool", N8N, base_url="https://n8n.example.com",
            owner=instances.SHARED_OWNER, shared=True, all_projects=True,
        )  # fmt: skip


def test_a_left_out_client_cant_use_it_not_even_a_new_project(everywhere: None) -> None:
    assert all(map(can_use, ("acme-web", "acme-app", "globex-portal", "seaside-messages")))

    leave_out(clients=["  acme "])  # a client is matched ignoring case and spaces
    assert not any(map(can_use, ("acme-web", "acme-app")))
    assert all(map(can_use, ("globex-portal", "seaside-messages")))

    with session_scope() as session:  # created after: left out too
        make_project(session, "acme-shop", owner="ACME")
        make_project(session, "initech-site", owner="initech")  # others still get it
    assert not can_use("acme-shop")
    assert can_use("initech-site")

    # Nor can it be linked by hand while left out.
    with pytest.raises(RuleViolationError, match="all projects except some"):
        bind("acme-shop", "tool")


def test_giving_it_back_restores_access(everywhere: None) -> None:
    leave_out(clients=["acme"], projects=["seaside-messages"])
    with session_scope() as session:
        make_project(session, "acme-shop", owner="acme")
    assert not any(map(can_use, ("acme-web", "acme-app", "acme-shop", "seaside-messages")))

    leave_out()
    assert all(map(can_use, ("acme-web", "acme-app", "acme-shop", "seaside-messages")))


def test_leaving_out_a_project_leaves_its_client_s_others_alone(everywhere: None) -> None:
    leave_out(projects=["acme-web"])
    assert not can_use("acme-web")
    assert can_use("acme-app")
    with session_scope() as session:
        make_project(session, "acme-shop", owner="acme")
    assert can_use("acme-shop")


def test_only_for_all_projects_and_only_real_projects(everywhere: None) -> None:
    with pytest.raises(RuleViolationError, match="no project named"):
        leave_out(projects=["no-such-project"])
    with session_scope() as session:
        tool = instances.get_instance(session, "tool")
        data = InstanceIn.model_validate(tool, from_attributes=True).model_copy(
            update={"all_projects": False, "except_clients": ["acme"]}
        )
        with pytest.raises(RuleViolationError, match="isn't for all projects"):
            instances.update_instance(session, data)


def test_the_connection_says_what_it_reaches(owner: TestClient) -> None:
    for name, client in (("acme-web", "Acme"), ("seaside-messages", "Seaside")):
        assert owner.post("/projects", json={"name": name, "owner": client}).status_code == 201
    created = owner.post(
        "/connections",
        json={"name": "tool", "type": "n8n", "env": "dev", "owner": "you", "secret": "k",
              "base_url": "https://n8n.example.com", "shared": True, "all_projects": True},
    )  # fmt: skip
    assert created.status_code == 201, created.text
    assert created.json()["reach"] == "All projects"

    changed = owner.patch(
        "/connections/tool",
        json={"except_clients": ["Acme"], "except_projects": ["seaside-messages"]},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["reach"] == "All projects except Acme (client), Seaside messages"
    assert body["projects"] == []

    # Turning "all projects" off drops the exceptions with it.
    off = owner.patch("/connections/tool", json={"all_projects": False}).json()
    assert (off["except_clients"], off["except_projects"], off["reach"]) == ([], [], "")
