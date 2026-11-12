"""Honest outcomes: a task that couldn't do what was asked ends "not done", says why where
it was asked from, and offers one click when the blocker is a connection the client has
but the project isn't linked to."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.channels import news
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import InstanceType, ProjectInstance, Task, TaskStatus
from ringkeeper.services import link_offer, tasks
from ringkeeper.wording import task_status
from ringkeeper.workers.manager import Collected, outcome_status
from tests import test_web_api
from tests.helpers import make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

pytestmark = pytest.mark.usefixtures("task_registry")


@pytest.fixture(autouse=True)
def world(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-site", owner="General Informatics LLC", title="Website")
        make_instance(
            session, "gi-google", type=InstanceType.GOOGLE, owner="General Informatics LLC",
            title="Google · info@geninfos.example",
        )  # fmt: skip


OFFER = "Link General Informatics LLC's Google · info@geninfos.example to Website and retry"


def not_done_task() -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="gi-site", prompt="email them")
        task.status, task.finished_at = TaskStatus.NOT_DONE, datetime.now(UTC)
        task.error = "There's no email connection on this project."
        task.offer = link_offer.find(session, task, "email")
        return str(task.id)


def test_saying_not_done_never_ends_as_done() -> None:
    job = type("Job", (), {"max_runtime_s": 60, "budget_usd": None, "max_turns": 10})()
    ran = {"subtype": "success", "is_error": False}
    done = Collected(result=ran, exit_code=0)
    assert outcome_status(job, done, None) == (TaskStatus.SUCCEEDED, None)  # type: ignore[arg-type]
    said = Collected(result=ran, exit_code=0, not_done="No email connection.")
    assert outcome_status(job, said, None) == (TaskStatus.NOT_DONE, "No email connection.")  # type: ignore[arg-type]
    assert task_status(TaskStatus.NOT_DONE) == "not done"


def test_the_client_s_connection_is_offered_and_one_click_links_and_retries() -> None:
    ident = not_done_task()
    with session_scope() as session:
        task = session.get_one(Task, ident)
        assert task.offer is not None
        assert task.offer["label"] == OFFER
        again = link_offer.take(session, get_settings(), ident)
        assert again.project == "gi-site"
        assert again.prompt == "email them"
        assert session.get_one(Task, ident).offer is None  # once
        assert session.scalars(select(ProjectInstance)).one().instance.name == "gi-google"


def test_no_offer_for_what_the_client_doesn_t_have() -> None:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="gi-site", prompt="x")
        assert link_offer.find(session, task, "airtable") is None
        assert link_offer.find(session, task, "") is None


def test_the_chat_gets_the_reason_and_the_button() -> None:
    ident = not_done_task()
    with session_scope() as session:
        task = session.get_one(Task, ident)
        said = news.result_text(task, None)
        button = news.offer_button(task)
    assert said.startswith("✖ Website: not done\nThere's no email connection on this project.")
    assert said.endswith(
        "Link General Informatics LLC's Google · info@geninfos.example to Website and retry?"
    )
    assert button is not None
    assert button[1] == f"rkl:{ident}"
    assert news.take_offer(get_settings(), ident) == "Linked. Running it again in Website."
    assert news.take_offer(get_settings(), ident).startswith("Not done:")  # only once


def test_the_task_page_offers_it(owner: TestClient) -> None:
    ident = not_done_task()
    card = owner.get(f"/tasks/{ident}").json()
    assert card["status"] == "not_done"
    assert card["offer"]["instance"] == "gi-google"
    again = owner.post(f"/tasks/{ident}/link-and-retry")
    assert again.status_code == 201, again.text
    assert again.json()["project"] == "gi-site"


def test_a_missing_automation_tool_recommends_airtable_s_own_server() -> None:
    """The built-in Airtable can't see automations; Airtable's own server can. When a task
    ends not done over that, and the client hasn't added the server, it says so with a link."""
    with session_scope() as session:
        make_instance(session, "gi-airtable", type=InstanceType.AIRTABLE,
                      owner="General Informatics LLC")  # fmt: skip
    from tests.helpers import bind  # noqa: PLC0415

    bind("gi-site", "gi-airtable")
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="gi-site", prompt="automations?")
        task.status = TaskStatus.NOT_DONE
        task.error = "The Airtable tools here can't list automations."
        offer = link_offer.find(session, task, "")
        assert offer is not None
        assert offer["recommend"] == "airtable"
        assert offer["label"].startswith("Airtable's own server can see automations")
        assert offer["href"].startswith("/connections/new?service=airtable&client=")
        assert "instance" not in offer  # nothing to link and retry
        make_instance(session, "gi-airtable-mcp", type=InstanceType.MCP,
                      owner="General Informatics LLC", config={"remote": "airtable"})  # fmt: skip
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="gi-site", prompt="again")
        task.error = "The Airtable tools here can't list automations."
        assert link_offer.find(session, task, "") is None  # the client has the server now
