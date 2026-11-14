"""A request that needs a kind of connection its project doesn't have (ads, email, a database)
is answered straight away with a button to add one, not started as a task that reads the
repository and ends Not done; work in an outside account is ops, never a feature; and a task
that does end Not done for want of a connection offers the same button."""

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Decision,
    DecisionKind,
    InstanceType,
    Message,
    MessageRole,
    Task,
    TaskKind,
    TaskStatus,
)
from ringkeeper.router.service import route_next
from ringkeeper.services import inbox, link_offer, needs, tasks, threads
from ringkeeper.services.inbox import Answer, Via
from tests import test_web_api
from tests.fake_router import FakeModel, output, routed
from tests.helpers import bind, make_instance, make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

pytestmark = pytest.mark.usefixtures("task_registry")

CLIENT = "Harbor Bakery"
ADS_REQUEST = "Set up two Meta ad campaigns for the spring sale"


@pytest.fixture(autouse=True)
def world(database_url: str) -> None:
    with session_scope() as session:
        # A project with a code folder: what made the router call ads work a feature.
        make_project(
            session, "hb-site", owner=CLIENT, title="Bakery site", workspace_path="hb-site"
        )
        make_project(session, "hb-shop", owner=CLIENT, title="Bakery shop")


def ask(body: str) -> str:
    with session_scope() as session:
        return str(threads.post_message(session, body, author="alice", source="cli").thread_id)


def route(*answers: dict[str, Any]) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def ads_task(project: str = "hb-site", confidence: float = 0.97, **extra: Any) -> dict[str, Any]:
    return routed(
        project, confidence, kind="feature", needs=["meta_ads"], prompt=ADS_REQUEST, **extra
    )


def all_tasks() -> list[Task]:
    with session_scope() as session:
        rows = list(session.scalars(select(Task)).all())
        session.expunge_all()
        return rows


def messages(thread_id: str) -> list[Message]:
    with session_scope() as session:
        rows = list(threads.thread_messages(session, uuid.UUID(thread_id)))
        session.expunge_all()
        return rows


def add_ads_connection(name: str = "hb-meta-ads", project: str = "hb-site") -> None:
    with session_scope() as session:
        make_instance(
            session, name, type=InstanceType.MCP, owner=CLIENT, config={"remote": "pipeboard"}
        )
    bind(project, name)


def test_a_request_for_ads_in_a_project_without_them_starts_nothing_and_says_so() -> None:
    thread_id = ask(ADS_REQUEST)
    route(output(ads_task()))
    assert all_tasks() == []  # no planner, no worker, no reading the repository
    asked, reply = messages(thread_id)[-2:]
    assert reply.role is MessageRole.ROUTER
    # What it looked for, and what the project has.
    assert reply.body == (
        "This project has no Meta Ads connection, and no connections linked at all."
    )
    assert reply.routing is not None
    [shown] = reply.routing["missing"]
    assert shown["label"] == "Add a Meta Ads connection to Bakery site"
    query = parse_qs(urlsplit(str(shown["href"])).query)
    # The add form for that client, linking what's added to this project.
    assert query["client"] == ["harbor-bakery"]  # the client, as the page names it
    assert query["link"] == ["hb-site"]
    assert query["service"] == [needs.add_target("meta_ads")]
    assert query["run"] == [str(asked.id)]
    assert "task" not in shown  # the worker's prompt stays out of what the page reads
    assert asked.routing is not None
    assert asked.routing["missing"][0]["task"]["prompt"] == ADS_REQUEST


def test_the_held_request_runs_once_the_connection_is_added() -> None:
    thread_id = ask(ADS_REQUEST)
    route(output(ads_task()))
    asked = messages(thread_id)[-2]
    with session_scope() as session, pytest.raises(RuleViolationError) as still:
        needs.run_waiting(session, get_settings(), str(asked.id))
    assert str(still.value) == "Bakery site has no Meta Ads connection yet."
    assert all_tasks() == []

    add_ads_connection()
    with session_scope() as session:
        [task] = needs.run_waiting(session, get_settings(), str(asked.id))
        assert task.project == "hb-site"
        assert task.prompt == ADS_REQUEST
        assert task.kind is TaskKind.OPS
    said = messages(thread_id)
    assert said[-1].body == "Working on it in Bakery site."
    reply = said[-2]
    assert reply.routing is not None
    assert reply.routing["missing"] == []  # its buttons are spent
    assert reply.routing["ran"] is True
    with session_scope() as session, pytest.raises(RuleViolationError):
        needs.run_waiting(session, get_settings(), str(asked.id))  # once


def test_ads_work_is_ops_even_in_a_project_with_a_code_folder() -> None:
    add_ads_connection()
    ask(ADS_REQUEST)
    route(output(ads_task()))
    [task] = all_tasks()
    assert task.kind is TaskKind.OPS  # no planner, no branch: the work is in the ad account
    assert task.project == "hb-site"


def test_code_work_stays_a_feature() -> None:
    ask("Add a newsletter sign-up form to the footer")
    route(output(routed("hb-site", 0.97, kind="feature")))
    [task] = all_tasks()
    assert task.kind is TaskKind.FEATURE


def test_two_projects_say_which_one_lacks_it() -> None:
    add_ads_connection(project="hb-shop")
    thread_id = ask("Run the spring sale ads for the site and the shop")
    route(output(ads_task("hb-site"), ads_task("hb-shop")))
    assert [t.project for t in all_tasks()] == ["hb-shop"]
    body = messages(thread_id)[-1].body
    assert "Bakery site has no Meta Ads connection" in body
    assert "Working on it in Bakery shop." in body


@pytest.mark.parametrize(
    ("kind", "connection", "fits"),
    [
        ("meta_ads", {"type": InstanceType.MCP, "config": {"remote": "pipeboard"}}, True),
        ("meta_ads", {"type": InstanceType.MCP, "config": {"remote": "notion"}}, False),
        # A server Ringkeeper can't tell the kind of counts by its name...
        ("database", {"type": InstanceType.MCP, "config": {}, "title": "Bakery Postgres"}, True),
        ("payments", {"type": InstanceType.HTTP, "config": {}, "title": "Stripe API"}, True),
        ("records", {"type": InstanceType.MCP, "config": {}, "title": "Stripe tools"}, False),
        # ... and one whose name says nothing might do it: unsure, so it counts.
        ("records", {"type": InstanceType.MCP, "config": {}, "title": "Bakery tools"}, True),
        # A Recommended service is what its key says, whatever it's called.
        ("payments", {"type": InstanceType.MCP, "config": {"remote": "notion"},
                      "title": "Payment notes"}, False),
        ("email", {"type": InstanceType.MAILBOX, "config": {}}, True),
        ("calendar", {"type": InstanceType.VENDOR, "config": {"vendor": "microsoft"}}, True),
        ("google_ads", {"type": InstanceType.VENDOR, "config": {"vendor": "google_ads"}}, True),
        ("ads", {"type": InstanceType.VENDOR, "config": {"vendor": "google_ads"}}, True),
        ("ads", {"type": InstanceType.VENDOR, "config": {"vendor": "facebook"}}, False),
    ],
)  # fmt: skip
def test_which_connections_give_what(kind: str, connection: dict[str, Any], fits: bool) -> None:
    with session_scope() as session:
        found = make_instance(session, "hb-thing", owner=CLIENT, **connection)
        assert needs.fits(found, kind) is fits


def test_answering_which_project_holds_it_too() -> None:
    thread_id = ask(ADS_REQUEST)
    unsure = ads_task(confidence=0.4, alternatives=[{"project": "hb-shop", "confidence": 0.3}])
    route(output(unsure))
    with session_scope() as session:
        [decision] = session.scalars(
            select(Decision).where(Decision.kind == DecisionKind.ROUTE)
        ).all()
        answered = inbox.answer(
            session, get_settings(), str(decision.id), Answer(choice="hb-site"), by="owner",
            via=Via.API,
        )  # fmt: skip
        assert answered.task is None
    assert all_tasks() == []
    reply = messages(thread_id)[-1]
    assert reply.body.startswith("This project has no Meta Ads connection")
    assert reply.routing is not None
    assert reply.routing["missing"][0]["project"] == "hb-site"


def not_done(lacked: str) -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="hb-site", prompt=ADS_REQUEST)
        task.status, task.finished_at = TaskStatus.NOT_DONE, datetime.now(UTC)
        task.error = "There's no Meta Ads connection on this project."
        task.offer = link_offer.find(session, task, lacked)
        return str(task.id)


def test_a_task_not_done_for_want_of_one_offers_the_add_button() -> None:
    ident = not_done("meta_ads")
    with session_scope() as session:
        offer = session.get_one(Task, ident).offer
    assert offer is not None
    assert offer["add"] == "meta_ads"
    assert offer["label"] == "Add a Meta Ads connection to Bakery site"
    query = parse_qs(urlsplit(str(offer["href"])).query)
    assert query["client"] == ["harbor-bakery"]  # the client, as the page names it
    assert query["link"] == ["hb-site"]
    assert query["run"] == [f"task:{ident}"]

    with session_scope() as session, pytest.raises(RuleViolationError):
        link_offer.run_again(session, get_settings(), ident)  # still none
    add_ads_connection()
    with session_scope() as session:
        again = link_offer.run_again(session, get_settings(), ident)
        assert again.project == "hb-site"
        assert session.get_one(Task, ident).offer is None  # once


def test_the_task_page_and_the_conversation_can_run_it(owner: TestClient) -> None:
    thread_id = ask(ADS_REQUEST)
    route(output(ads_task()))
    asked = messages(thread_id)[-2]
    refused = owner.post(f"/messages/{asked.id}/run-now")
    assert refused.status_code == 409
    assert "no Meta Ads connection yet" in refused.json()["detail"]
    add_ads_connection()
    started = owner.post(f"/messages/{asked.id}/run-now")
    assert started.status_code == 201
    assert len(started.json()["tasks"]) == 1

    ident = not_done("meta_ads")
    card = owner.get(f"/tasks/{ident}").json()
    assert card["offer"]["add"] == "meta_ads"
    again = owner.post(f"/tasks/{ident}/run-again")
    assert again.status_code == 201


# Real use (beta.44): "In the Leads table of Airtable, find records that are not leads..." in
# a project with Airtable linked was held as "no database connection".
LEADS_REQUEST = "In the Leads table of Airtable, find records that are not leads for the bakery"


def link(name: str, project: str = "hb-shop", **connection: Any) -> None:
    connection.setdefault("type", InstanceType.MCP)
    with session_scope() as session:
        make_instance(session, name, owner=CLIENT, **connection)
    bind(project, name)


def link_the_shops_connections() -> None:
    link("hb-airtable", type=InstanceType.AIRTABLE, title="Airtable · Harbor Bakery")
    link("hb-google", type=InstanceType.GOOGLE, title="Google · Harbor Bakery")
    link("hb-n8n", type=InstanceType.N8N, title="n8n · Harbor Bakery")


def records_task(**extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {"kind": "data", "prompt": LEADS_REQUEST, **extra}
    return routed("hb-shop", 0.95, **fields)


@pytest.mark.parametrize(
    "said",
    [
        # As the router answered then: "database", and its prompt named the connection.
        {"needs": ["database"], "prompt": f"Use hb-airtable. {LEADS_REQUEST}"},
        # The connection the routing call picked.
        {"needs": ["records"], "connections": ["hb-airtable"]},
        # The service the message names.
        {"needs": ["records"], "service": "Airtable"},
        # Only the kind: Airtable is a table of records.
        {"needs": ["database"]},
    ],
)
def test_a_project_that_has_airtable_runs_the_airtable_request(said: dict[str, Any]) -> None:
    link_the_shops_connections()
    thread_id = ask(LEADS_REQUEST)
    route(output(records_task(**said)))
    [task] = all_tasks()
    assert task.project == "hb-shop"
    assert "no database connection" not in messages(thread_id)[-1].body


def test_a_named_service_only_that_service_does() -> None:
    link("hb-db", type=InstanceType.SUPABASE, title="Supabase · Harbor Bakery")
    link("hb-google", type=InstanceType.GOOGLE, title="Google · Harbor Bakery")
    thread_id = ask(LEADS_REQUEST)
    route(output(records_task(needs=["records"], service="Airtable")))
    assert all_tasks() == []  # a database has records, but the message asked for Airtable
    assert messages(thread_id)[-1].body == (
        "This project has no Airtable connection. It has Google and Supabase, and none of them "
        "is Airtable."
    )


def test_the_reply_says_what_it_looked_for_and_what_the_project_has() -> None:
    link_the_shops_connections()
    thread_id = ask("Charge the deposit for the wedding cake order")
    route(output(routed("hb-shop", 0.95, kind="ops", needs=["payments"])))
    assert all_tasks() == []
    assert messages(thread_id)[-1].body == (
        "This project has no payments connection. It has Airtable, Google and n8n, and none of "
        "them can do payments."
    )


# A word that maps to several kinds of connection: any one of them linked will do.
@pytest.mark.parametrize(
    ("word", "kind", "connection"),
    [
        ("table", "records", {"type": InstanceType.AIRTABLE}),
        ("table", "records", {"type": InstanceType.SUPABASE}),
        ("records", "records", {"config": {"remote": "notion"}}),
        ("records", "records", {"type": InstanceType.VENDOR, "config": {"vendor": "hubspot"}}),
        ("records", "records", {"type": InstanceType.GOOGLE}),
        ("sheet", "spreadsheet", {"type": InstanceType.GOOGLE}),
        ("sheet", "spreadsheet", {"type": InstanceType.AIRTABLE}),
        ("sheet", "spreadsheet", {"type": InstanceType.VENDOR, "config": {"vendor": "microsoft"}}),
        ("mail", "email", {"type": InstanceType.GOOGLE}),
        ("mail", "email", {"type": InstanceType.MAILBOX}),
        ("mail", "email", {"type": InstanceType.VENDOR, "config": {"vendor": "microsoft"}}),
        ("calendar", "calendar", {"type": InstanceType.GOOGLE}),
        ("calendar", "calendar", {"config": {"remote": "calcom"}}),
        ("calendar", "calendar", {"type": InstanceType.VENDOR, "config": {"vendor": "microsoft"}}),
        ("files", "files", {"type": InstanceType.GOOGLE}),
        ("files", "files", {"type": InstanceType.S3}),
        ("files", "files", {"type": InstanceType.VENDOR, "config": {"vendor": "microsoft"}}),
    ],
)  # fmt: skip
def test_any_fitting_connection_meets_the_word(
    word: str, kind: str, connection: dict[str, Any]
) -> None:
    link("hb-thing", **connection)
    ask(f"Do something with the {word} for the spring sale")
    route(output(routed("hb-shop", 0.95, kind="data", needs=[kind])))
    assert [t.project for t in all_tasks()] == ["hb-shop"], (word, connection)


@pytest.mark.parametrize("kind", ["records", "spreadsheet", "email", "calendar", "files"])
def test_with_nothing_of_that_kind_linked_it_is_held(kind: str) -> None:
    link("hb-pay", config={"remote": "stripe"}, title="Stripe · Harbor Bakery")
    ask("Do something for the spring sale")
    route(output(routed("hb-shop", 0.95, kind="data", needs=[kind])))
    assert all_tasks() == []
