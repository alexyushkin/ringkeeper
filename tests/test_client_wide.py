"""A client's connection for all of that client's projects, new ones too, maybe except some.
It never reaches another client's, and a project's link can still be narrowed."""

import pytest
from fastapi.testclient import TestClient

from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import bindings, instances, projects
from tests import test_web_api
from tests.helpers import make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


@pytest.fixture
def acme(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_project(session, "acme-shop", owner="acme")
        make_project(session, "globex-app", owner="globex")
        make_instance(
            session, "acme-api", type=InstanceType.AIRTABLE, secret_ref="file://acme/a",
            all_projects=True, except_projects=["acme-shop"],
        )  # fmt: skip


def linked() -> list[str]:
    with session_scope() as session:
        return instances.bound_project_names(instances.get_instance(session, "acme-api"))


def test_it_reaches_its_clients_projects_and_no_one_elses(acme: None) -> None:
    assert linked() == ["acme-web"]
    with session_scope() as session:
        found = instances.get_instance(session, "acme-api")
        globex = projects.get_project(session, "globex-app")
        assert instances.excluded(found, globex)


def test_a_new_project_gets_it_unless_its_another_clients(acme: None) -> None:
    with session_scope() as session:
        make_project(session, "acme-blog", owner="Acme")  # the same client, written differently
        make_project(session, "globex-blog", owner="globex")
    assert linked() == ["acme-blog", "acme-web"]


def test_the_card_says_what_it_reaches(acme: None) -> None:
    with session_scope() as session:
        found = instances.get_instance(session, "acme-api")
        assert instances.describe_reach(found) == "All acme projects except Acme shop"
        found.except_projects = []
        assert instances.describe_reach(found) == "All acme projects"


def test_a_link_can_still_be_narrowed_and_stays_narrowed(acme: None) -> None:
    with session_scope() as session:
        bindings.set_binding(
            session, BindingIn(project="acme-web", instance="acme-api", scope={"base": "appX"})
        )
        found = instances.get_instance(session, "acme-api")
        instances.link_everywhere(session, found)
        (link,) = found.bindings
        assert link.scope == {"base": "appX"}


def test_it_cant_leave_out_other_clients(database_url: str) -> None:
    data = InstanceIn(
        name="acme-x", type=InstanceType.AIRTABLE, env=Env.DEV, secret_ref="file://acme/x",
        owner="acme", all_projects=True, except_clients=["globex"],
    )  # fmt: skip
    with session_scope() as session, pytest.raises(RuleViolationError, match="no other clients"):
        instances.create_instance(session, data)


def test_turning_it_on_from_the_success_card(owner: TestClient, database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_project(session, "acme-shop", owner="acme")
        make_instance(session, "acme-api", type=InstanceType.AIRTABLE, secret_ref="file://acme/a")
    body = owner.patch(
        "/connections/acme-api", json={"all_projects": True, "except_projects": ["acme-shop"]}
    ).json()
    assert body["projects"] == ["acme-web"]
    assert body["reach"] == "All acme projects except Acme shop"
    body = owner.patch("/connections/acme-api", json={"all_projects": False}).json()
    assert body["except_projects"] == []
    assert body["reach"] == ""
