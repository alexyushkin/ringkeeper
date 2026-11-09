"""Clients of their own: listed with or without projects, named as typed, renamed,
moved and removed; and projects renamed everywhere they are named."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.db import session_scope
from ringkeeper.models import Decision, DecisionKind, Instance, Project, Task
from ringkeeper.schemas import BindingIn
from ringkeeper.services import bindings, clients, inbox, projects
from tests import test_web_api
from tests.helpers import make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


def listed(app: TestClient) -> dict[str, dict[str, object]]:
    return {c["name"]: c for c in app.get("/clients").json()}


def test_a_client_counts_your_tools_its_projects_use(session: Session) -> None:
    make_project(session, "beta-one", owner="Beta")
    make_project(session, "beta-two", owner="Beta")
    make_project(session, "other-one", owner="Other")
    make_instance(session, "beta-db", owner="beta")
    make_instance(session, "tool", shared=True, owner="you")
    make_instance(session, "unused", shared=True, owner="you")
    for project in ("beta-one", "beta-two"):  # two links, still one connection
        bindings.set_binding(session, BindingIn(project=project, instance="tool"))
    bindings.set_binding(session, BindingIn(project="beta-one", instance="beta-db"))
    counts = {c.slug: c.connections for c in clients.list_clients(session)}
    assert counts == {"beta": 2, "other": 0}


def test_a_client_is_named_as_typed_and_listed_without_projects(owner: TestClient) -> None:
    made = owner.post("/clients", json={"name": "Seaside Windows & Doors"})
    assert made.status_code == 201, made.text
    assert made.json() == {
        "slug": "seaside-windows-doors", "name": "Seaside Windows & Doors", "projects": 0,
        "connections": 0, "month_usd": 0.0, "cap_usd": None, "default_project": None, "own": False,
    }  # fmt: skip
    # Typing it again, in any case, is the same client.
    assert owner.post("/clients", json={"name": "seaside windows & doors"}).json()["slug"] == (
        "seaside-windows-doors"
    )
    # A connection for a new client creates the client; it has no projects yet.
    added = owner.post(
        "/connections",
        json={"name": "gi-n8n", "type": "n8n", "env": "dev", "owner": "General Informatics LLC",
              "secret": "k", "base_url": "https://n8n.example.com"},
    )  # fmt: skip
    assert added.status_code == 201, added.text
    assert added.json()["owner"] == "general-informatics-llc"
    clients_now = listed(owner)
    assert clients_now["General Informatics LLC"]["connections"] == 1
    assert clients_now["General Informatics LLC"]["projects"] == 0
    assert clients_now["Seaside Windows & Doors"]["connections"] == 0
    # A project for an existing client, typed by its name, uses its slug.
    project = owner.post(
        "/projects", json={"name": "seaside-site", "owner": "Seaside Windows & Doors"}
    )
    assert project.status_code == 201, project.text
    assert owner.get("/projects/seaside-site").json()["owner"] == "seaside-windows-doors"


def test_rename_move_and_delete_a_client(owner: TestClient) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    renamed = owner.patch("/clients/acme", json={"name": "Acme Corporation"})
    assert renamed.json()["name"] == "Acme Corporation"
    assert renamed.json()["slug"] == "acme"  # URLs and IDs stay
    owner.post("/clients", json={"name": "Globex"})
    clash = owner.patch("/clients/acme", json={"name": "globex"})
    assert clash.status_code == 409
    assert "already a client named 'Globex'" in clash.text

    refused = owner.delete("/clients/acme")
    assert refused.status_code == 409
    assert "still has 1 project(s)" in refused.text
    moved = owner.post("/clients/acme/move", json={"to": "Globex"})
    assert moved.status_code == 200, moved.text
    assert owner.get("/projects/acme-web").json()["owner"] == "globex"
    assert owner.delete("/clients/acme").status_code == 204
    assert "Acme Corporation" not in listed(owner)


@pytest.mark.usefixtures("database_url")
def test_owners_match_by_slug() -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="Acme")
        make_instance(session, "acme-db", owner="acme")
        assert session.scalar(select(Project.owner)) == "acme"
        assert session.scalar(select(Instance.owner).where(Instance.name == "acme-db")) == "acme"
        assert clients.names(session) == {"acme": "Acme"}


@pytest.mark.usefixtures("database_url")
def test_renaming_a_project_renames_it_everywhere() -> None:
    from ringkeeper.config import get_settings  # noqa: PLC0415

    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_project(session, "acme-app", owner="acme")
        make_instance(session, "tool", shared=True, owner="you", all_projects=True,
                      except_projects=["acme-web"])  # fmt: skip
        task = Task(project="acme-web", prompt="p", status="succeeded", auth_mode="api_key",
                    max_turns=1, max_runtime_s=60, changed_files=[])  # fmt: skip
        session.add(task)
        inbox.create(
            session, get_settings(), DecisionKind.ROUTE,
            {"message": "m", "candidates": [{"project": "acme-web", "confidence": 0.4}],
             "task": {"prompt": "p"}},
        )  # fmt: skip
    with session_scope() as session:
        projects.rename_project(session, "acme-web", "acme-site")
    with session_scope() as session:
        renamed = projects.get_project(session, "acme-site")
        assert "acme-web" in renamed.aliases  # asking by the old name still finds it
        assert session.scalar(select(Task.project)) == "acme-site"
        decision = session.scalars(select(Decision)).one()
        assert inbox.options(decision) == ["acme-site", "none"]
        tool = session.scalars(select(Instance).where(Instance.name == "tool")).one()
        assert tool.except_projects == ["acme-site"]


@pytest.mark.usefixtures("database_url")
def test_a_project_isn_t_renamed_while_a_task_is_at_work() -> None:
    from ringkeeper.errors import RuleViolationError  # noqa: PLC0415

    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        session.add(Task(project="acme-web", prompt="p", status="running", auth_mode="api_key",
                         max_turns=1, max_runtime_s=60, changed_files=[]))  # fmt: skip
    with session_scope() as session, pytest.raises(RuleViolationError, match="running or waiting"):
        projects.rename_project(session, "acme-web", "acme-site")


def test_rename_a_project_through_the_api(owner: TestClient) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    owner.post("/projects", json={"name": "acme-app", "owner": "acme"})
    taken = owner.post("/projects/acme-web/rename", json={"name": "acme-app"})
    assert taken.status_code == 409
    done = owner.post("/projects/acme-web/rename", json={"name": "acme-site"})
    assert done.json() == {"name": "acme-site"}
    assert owner.get("/projects/acme-site").status_code == 200
    assert owner.get("/projects/acme-web").status_code == 404
