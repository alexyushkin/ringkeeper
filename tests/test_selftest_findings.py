"""What the self-test on beta.35 found, fixed: a project's own name beats an alias that's
part of it, the same request isn't started twice, work on one of a client's projects is
asked about however sure the model is, and declining to cross clients passes."""

from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import (
    Decision,
    DecisionKind,
    DecisionStatus,
    InstanceType,
    Message,
    Project,
    Task,
)
from ringkeeper.router import follow_up
from ringkeeper.router.prompt import SYSTEM_PROMPT
from ringkeeper.router.service import route_next
from ringkeeper.scenarios.catalog import SCENARIOS
from ringkeeper.scenarios.checks import ALLOWED, DECLINED, Evidence, judge
from ringkeeper.services import clients, threads
from ringkeeper_worker.agent import HEADLESS_NOTE
from tests.fake_router import FakeModel, output, routed
from tests.helpers import bind, make_instance, make_project

ADD = "Add a file called notes.txt saying hi to Demo Co's project folder."


@pytest.fixture(autouse=True)
def projects(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "demo-shop", owner="Demo Co", title="Demo shop")
        make_project(session, "demo-site", owner="Demo Co", title="Demo site")
        make_project(session, "seaside-web", owner="Seaside", title="Seaside website")
        make_project(session, "seaside-crm", owner="Seaside", title="CRM")
        session.scalars(select(Project).where(Project.name == "demo-shop")).one().aliases = [
            "demo", "shop",
        ]  # fmt: skip
        session.scalars(select(Project).where(Project.name == "seaside-crm")).one().aliases = [
            "seaside",
        ]  # fmt: skip


def ask(body: str, thread: str | None = None) -> str:
    with session_scope() as session:
        message = threads.post_message(session, body, author="alice", source="web", thread=thread)
        return str(message.thread_id)


def route(*answers: dict[str, Any]) -> None:
    assert route_next(get_settings(), FakeModel(list(answers))) is not None


def started() -> list[str]:
    with session_scope() as session:
        return [t.project for t in session.scalars(select(Task).order_by(Task.created_at))]


def open_questions() -> int:
    with session_scope() as session:
        return len(
            session.scalars(
                select(Decision).where(
                    Decision.kind == DecisionKind.ROUTE, Decision.status == DecisionStatus.PENDING
                )
            ).all()
        )


def last_reply(thread: str) -> str:
    with session_scope() as session:
        return threads.thread_messages(session, thread)[-1].body  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("said", "project"),
    [
        ("Demo site", "demo-site"),  # not Demo shop, whose alias "demo" is part of it
        ("the Demo site one", "demo-site"),
        ("demo-site", "demo-site"),
        ("shop", "demo-shop"),
        ("Seaside website", "seaside-web"),  # not the CRM, whose alias "seaside" is part of it
        ("the seaside website please", "seaside-web"),
    ],
)
def test_the_most_specific_name_wins(said: str, project: str) -> None:
    with session_scope() as session:
        assert follow_up.named_project(session, said) == project


def test_a_client_s_name_isn_t_its_project_s_alias() -> None:
    with session_scope() as session:
        assert follow_up.named_in(session, ADD) == ([], ["demo-co"])
        assert follow_up.named_project(session, "Demo Co") is None  # two projects, no default


def test_just_the_project_s_name_answers_the_question() -> None:
    thread = ask(ADD)
    route(
        output(routed("demo-shop", 0.6, alternatives=[{"project": "demo-site", "confidence": 0.4}]))
    )
    assert open_questions() == 1
    ask("Demo site", thread=thread)
    route()  # no model call: the name answers it
    assert started() == ["demo-site"]
    assert open_questions() == 0


def test_the_same_request_again_doesn_t_start_the_same_work() -> None:
    thread = ask("Count the orders in Demo shop's database.")
    route(output(routed("demo-shop", 0.95, prompt="Count the orders.")))
    ask("Count the orders in Demo shop's database.", thread=thread)  # the same words again
    route(output(routed("demo-shop", 0.95, prompt="Count all the orders in the database.")))
    ask("Please count them", thread=thread)  # other words, the same work routed
    route(output(routed("demo-shop", 0.95, prompt="Count the orders.")))
    assert started() == ["demo-shop"]
    said = last_reply(thread)
    assert said.startswith("Already working on that: [Demo shop](/tasks/")
    ask("Now list the products", thread=thread)  # something else does start
    route(output(routed("demo-shop", 0.95, prompt="List the products.")))
    assert started() == ["demo-shop", "demo-shop"]


def test_project_work_for_a_client_asks_however_sure_the_model_is() -> None:
    ask(ADD)
    route(output(routed("demo-shop", 0.95)))  # project_specific by default
    assert started() == []
    assert open_questions() == 1


def test_client_level_work_still_goes_to_the_default_project() -> None:
    with session_scope() as session:
        clients.set_default_project(session, "demo-co", "demo-shop")
    ask("Read Demo Co's newest email and tell me its subject.")
    route(output(routed("demo-site", 0.95, project_specific=False)))
    assert open_questions() == 0
    assert len(started()) == 1


def test_naming_a_connection_decides_without_asking() -> None:
    with session_scope() as session:
        make_instance(
            session,
            "demo-crm",
            type=InstanceType.MCP,
            secret_ref="file://demo/crm",
            owner="demo-co",
        )
    bind("demo-shop", "demo-crm", is_default=True)
    ask("Use demo-crm to export Demo Co's contact list.")
    route(output(routed("demo-shop", 0.95)))
    assert started() == ["demo-shop"]
    assert open_questions() == 0


def test_naming_the_project_or_choosing_it_doesn_t_ask() -> None:
    ask("Add a file called notes.txt saying hi to Demo site's folder.")
    route(output(routed("demo-site", 0.95)))
    ask(ADD + " ")  # a client named, but the project chosen at the top
    with session_scope() as session:
        newest = session.scalars(select(Message).order_by(Message.created_at.desc())).first()
        assert newest is not None
        newest.target_project = "demo-shop"
    route(output(routed("demo-shop", 0.95)))
    assert started() == ["demo-site", "demo-shop"]
    assert open_questions() == 0


def _pushed() -> Any:
    return next(s for s in SCENARIOS if s.key == "pushed-by-record")


def test_declining_to_cross_passes_even_when_the_task_ends_not_done() -> None:
    scenario = _pushed()
    seen = Evidence(statuses=["not_done"], crossing=DECLINED, tools=["airtable"])
    assert not [w for w in judge(scenario, seen) if "ended as" in w]
    seen = Evidence(statuses=["not_done"], crossing=ALLOWED, tools=["airtable"])
    assert any("ended as" in w for w in judge(scenario, seen))


def test_a_name_that_didn_t_answer_is_said_plainly() -> None:
    scenario = next(s for s in SCENARIOS if s.key == "answer-by-name")
    seen = Evidence(asked=True, times_asked=1, name_ignored=True)
    assert "the reply with just the project's name didn't answer the question" in judge(
        scenario, seen
    )


def test_the_router_doesn_t_invent_limits() -> None:
    assert "Don't add limits the operator didn't state" in SYSTEM_PROMPT
    assert 'never "reply with\n  it"' in SYSTEM_PROMPT


def test_the_worker_knows_its_final_message_is_the_answer() -> None:
    assert "Your final message is what the operator reads" in HEADLESS_NOTE
    assert "That isn't a request to send an email" in HEADLESS_NOTE
    # Checked first, then said once: not "4 are active", then "no, three" in one message.
    assert "give it once: no correcting it mid-message" in HEADLESS_NOTE


def test_a_count_is_the_tool_s_total_not_a_hand_count() -> None:
    """From a real run: "Bayside (46)" and "Mr. Gutter (13)" for 49 and 10, though the
    items listed under each added up right."""
    note = " ".join(HEADLESS_NOTE.split())
    assert 'For "how many", call the tool with a filter and quote the total it returns' in note
    assert "never count items by reading a list" in note
    assert "A count printed next to a list must equal the items in that list" in note


def test_a_connection_is_reached_only_through_its_own_gateway() -> None:
    """From a real run: with no tool for the job, the worker tried "the Ringkeeper MCP route"
    for the Airtable connection too."""
    note = " ".join(HEADLESS_NOTE.split())
    assert "A connection is reached only through its own gateway endpoint" in note
    assert "stop there and say so" in note
    assert "with done saying what you did finish" in note


def test_a_list_request_gets_the_list_and_an_offer_of_detail() -> None:
    """From Check my setup: "List the tables" came back with every field of 39 tables."""
    note = " ".join(HEADLESS_NOTE.split())
    assert "Answer what was asked, at the size it was asked" in note
    assert '"list the tables" is their names, not every column of each' in note
    assert "offer them in one line at the end" in note
