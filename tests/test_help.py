"""The Help assistant: real questions find the right part of the guide, and what the guide
doesn't cover is never invented."""

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import MessageRole, Task
from ringkeeper.router.service import route_next
from ringkeeper.services import help as guide
from ringkeeper.services import threads
from tests.fake_router import FakeModel, output, routed
from tests.helpers import make_project
from tests.test_web_api import (  # noqa: F401 (fixtures)
    admin,  # pyright: ignore[reportUnusedImport]
    owner,  # pyright: ignore[reportUnusedImport]
    setup_code,  # pyright: ignore[reportUnusedImport]
)

ROOT = Path(__file__).parent.parent

# From UX_WALKTHROUGH.md and real use: the question, and the guide section that answers it.
QUESTIONS = [
    ("How do I link a connection to a project?", "Link a connection to a project"),
    ("How do I add a shared tool?", "Your own tools (shared tools)"),
    (
        "How do I use my Claude subscription?",
        "Can I use my Claude subscription instead of an API key?",
    ),
    ("How do I add a second Google account?", "Add a Google account"),
    ("Where are my agents?", "Agents"),
    ("How do I undo a change?", "Can I undo a change?"),
    ("How do I restore a backup?", "Restore on another computer"),
    (
        "What's the difference between an agent and a routine?",
        "What's the difference between an agent and a routine?",
    ),
    ("How do I leave one client out of a tool?", "Your own tools (shared tools)"),
    ("Can I approve from my phone?", "Can I approve from my phone?"),
    ("How do I make a routine run every morning?", "Routines"),
    ("How do I narrow a connection to one base?", "Narrowing it"),
    ("Why did my connection test fail?", "A connection's test fails"),
    ("What happens when a change touches live data?", "Can it change things without asking?"),
    ("How do I get the morning digest on Slack?", "Morning digest"),
    ("How do I attach a file to a request?", "Attach files to a request"),
    ("How do I use the quick request key?", "Quick request"),
    ("How do I trust an action for an hour?", "Trust an action for an hour"),
    ("Where do I see what a task changed?", "Where do I see what a task changed?"),
    ("How do I move to a new computer?", "How do I move to a new computer?"),
]

# Features that don't exist: answered without a model, so nothing can be made up.
NOT_THERE = [
    "How do I export my tasks to Excel?",
    "Can Ringkeeper make invoices for my clients?",
    "Is there an iPhone app?",
    "Can I add a teammate?",
    "Can I change the app to French?",
    "Can Ringkeeper print my approvals on a fax machine?",
    "How do I turn on the blockchain ledger?",
]


@pytest.mark.parametrize(("question", "section"), QUESTIONS)
def test_a_real_question_finds_its_section(question: str, section: str) -> None:
    assert section in [s.title for s in guide.search(question)[:3]]


@pytest.fixture
def database(database_url: str) -> None:
    del database_url


@pytest.mark.usefixtures("database")
@pytest.mark.parametrize("question", NOT_THERE)
def test_what_doesnt_exist_is_never_invented(question: str) -> None:
    model = FakeModel([])  # any call to it would fail the test
    with session_scope() as session:
        helped = guide.answer(session, get_settings(), model, question)
    assert helped.text.startswith((guide.NOT_DONE, guide.NO_DOCS))
    assert model.calls == []
    assert guide.SUGGEST in helped.actions
    assert guide.REPORT in helped.actions


@pytest.mark.usefixtures("database")
def test_connecting_something_the_guide_doesnt_name_still_gets_an_answer() -> None:
    model = FakeModel(["Search for it under Other connectors.\nSOURCES: 1"])
    with session_scope() as session:
        helped = guide.answer(session, get_settings(), model, "How do I connect Salesforce?")
    assert helped.text == "Search for it under Other connectors."
    assert helped.cites[0]["url"].endswith("docs/connections.md#adding-one")


@pytest.mark.usefixtures("database")
def test_an_answer_is_grounded_cited_and_ends_with_a_next_step() -> None:
    with session_scope() as session:
        make_project(session, "seaside-web", owner="seaside")
    model = FakeModel(["Open a client's Connections tab, then Link to project….\nSOURCES: 1, 2"])
    with session_scope() as session:
        helped = guide.answer(
            session, get_settings(), model, "How do I link a connection to a project?"
        )
    system, prompt, _ = model.calls[0]
    assert "Never invent a feature" in system
    assert "GUIDE:" in prompt
    assert "Link to project…" in prompt  # the section itself
    assert '"seaside-web"' in prompt  # and your setup
    assert "SOURCES" not in helped.text
    assert helped.cites[0]["title"] == "Link a connection to a project"
    assert helped.cites[0]["url"].endswith("docs/connections.md#link-a-connection-to-a-project")
    assert {"label": "Browse connectors", "href": "/connections/new"} in helped.actions


@pytest.mark.usefixtures("database")
def test_when_the_model_finds_nothing_it_offers_the_way_forward() -> None:
    model = FakeModel([guide.NO_DOCS])
    with session_scope() as session:
        helped = guide.answer(session, get_settings(), model, "How do I undo a project?")
    assert helped.text == guide.NO_DOCS
    assert helped.cites == []
    assert guide.SUGGEST in helped.actions


@pytest.mark.usefixtures("database")
def test_a_client_with_nothing_yet_gets_add_project_first() -> None:
    from ringkeeper.services import clients  # noqa: PLC0415

    with session_scope() as session:
        clients.ensure(session, "Seaside")
    model = FakeModel(["Seaside has no projects yet, so add one first.\nSOURCES: 1"])
    with session_scope() as session:
        helped = guide.answer(
            session, get_settings(), model, "How do I link a connection for Seaside?"
        )
    assert '"seaside": "Seaside"' in model.calls[0][1]  # the model knows it has none
    assert helped.actions[0] == {"label": "Add project", "href": "/projects/new?client=seaside"}


# --- Through the router: no project, no task, no approval ---


@pytest.mark.usefixtures("database")
def test_a_how_to_in_the_request_box_is_answered_without_a_task() -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        thread = threads.post_message(
            session, "How do I link a connection to a project?", author="me", source="web"
        ).thread_id
    # The router's model thought it was work; the plain how-to still comes here.
    model = FakeModel([output(routed("acme-web", 0.9)), "Use Link to project….\nSOURCES: 1"])
    assert route_next(get_settings(), model) is not None
    with session_scope() as session:
        assert session.query(Task).count() == 0
        reply = threads.recent_messages(session, thread, limit=5)[-1]
        assert reply.role is MessageRole.ROUTER
        assert reply.body == "Use Link to project…."
        assert reply.routing is not None
        assert reply.routing["help"]["cites"][0]["title"] == "Link a connection to a project"


def test_plain_how_tos_are_recognized_and_work_is_not() -> None:
    names = ["acme-web", "acme"]
    for question in ("How do I add a shared tool?", "Is there an iPhone app?"):
        assert guide.asks_how(question, names), question
    for message in ("Add a line to the site", "How do I reset the password in acme-web?"):
        assert not guide.asks_how(message, names), message


@pytest.mark.usefixtures("database")
def test_a_how_to_about_a_clients_own_thing_still_goes_to_the_project() -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        threads.post_message(
            session, "How do I reset the password in acme-web?", author="me", source="web"
        )
    assert route_next(get_settings(), FakeModel([output(routed("acme-web", 0.9))])) is not None
    with session_scope() as session:
        assert session.query(Task).count() == 1


def test_help_ask_answers_without_a_model_when_it_can(owner: TestClient) -> None:  # noqa: F811
    answered = owner.post("/help/ask", json={"question": "Is there an iPhone app?"})
    assert answered.status_code == 200, answered.text
    assert answered.json()["text"].startswith(guide.NOT_DONE)


# --- The index ---


def test_the_index_is_up_to_date_with_the_guide() -> None:
    spec = importlib.util.spec_from_file_location(
        "build_help_index", ROOT / "scripts" / "build_help_index.py"
    )
    assert spec is not None
    assert spec.loader is not None
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    shipped = (ROOT / "src" / "ringkeeper" / "help_index.json").read_text(encoding="utf-8")
    assert shipped == script.build(), "run: uv run python scripts/build_help_index.py"
