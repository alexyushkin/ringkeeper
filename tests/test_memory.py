"""Agent memory, here and in a fake Notion: writing, reading, sync both ways, isolation between two
clients' projects, the filter, and the shared notes."""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, cast

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.calls import Caller
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import (
    Agent,
    AuditEvent,
    AuthMode,
    Env,
    InstanceType,
    MemoryNote,
    SessionRole,
    Task,
    TaskStatus,
    WorkerSession,
)
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import instances, memory, notion_api, projects, secrets_in
from ringkeeper.vault import get_vault
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
PARENT = "11111111-1111-4111-8111-111111111111"
OTHER_PARENT = "22222222-2222-4222-8222-222222222222"
OUTSIDE = "99999999-9999-4999-8999-999999999999"
API_KEY = "sk-ant-api03-" + "abcdefghijklmnopqrstuvwxyz012345"


class FakeNotion:
    """Notion's REST API as its reference describes it: only what's shared can be reached."""

    def __init__(self) -> None:
        self.shared = {PARENT, OTHER_PARENT}
        self.databases: dict[str, dict[str, Any]] = {}  # data source -> its rows, by id
        self.titles: dict[str, str] = {}
        self.parents: dict[str, str] = {}  # a page's or data source's parent page
        self.bodies: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
        self.kinds: dict[str, str] = {}  # a row's Kind
        self.added_columns: list[dict[str, Any]] = []  # what PATCH /data_sources added
        self.down = False
        self.clock = 0

    def stamp(self) -> str:
        self.clock += 1
        return f"2026-11-06T10:{self.clock // 60:02}:{self.clock % 60:02}.000Z"

    def row(self, source: str, text: str, agent: str = "", task: str = "") -> str:
        """A row typed in Notion, or by another tool."""
        ident = str(uuid.uuid4())
        self.databases[source][ident] = {"text": text, "agent": agent, "task": task,
                                         "edited": self.stamp(), "in_trash": False}  # fmt: skip
        return ident

    def edit(self, source: str, ident: str, text: str) -> None:
        self.databases[source][ident] |= {"text": text, "edited": self.stamp()}

    def value(self, props: dict[str, Any], name: str) -> str:
        pieces = props.get(name, {}).get("rich_text") or [{"text": {"content": ""}}]
        return str(pieces[0]["text"]["content"])

    def page(self, ident: str, row: dict[str, Any]) -> dict[str, Any]:
        def rich(value: str) -> list[dict[str, str]]:
            return [{"plain_text": value}] if value else []

        return {
            "object": "page", "id": ident, "last_edited_time": row["edited"],
            "in_trash": row["in_trash"], "url": f"https://notion.so/{ident}",
            "properties": {
                "Note": {"type": "title", "title": rich(row["text"])},
                "Agent": {"type": "rich_text", "rich_text": rich(row["agent"])},
                "Source task": {"type": "rich_text", "rich_text": rich(row["task"])},
                "Date": {"type": "date", "date": {"start": "2026-11-06"}},
            },
        }  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path))
        assert request.headers["Notion-Version"] == notion_api.VERSION
        if self.down:
            return httpx2.Response(503, json={"message": "down"})
        if request.headers["Authorization"] != "Bearer ntn_fake":
            return httpx2.Response(401, json={"message": "API token is invalid."})
        body: dict[str, Any] = json.loads(request.content) if request.content else {}
        if path == "/databases" and request.method == "POST":
            parent = body["parent"]["page_id"]
            if parent not in self.shared:
                return httpx2.Response(404, json={"message": "not shared"})
            source, database = str(uuid.uuid4()), str(uuid.uuid4())
            self.databases[source] = {}
            self.parents[source] = parent
            self.titles[source] = body["title"][0]["text"]["content"]
            return httpx2.Response(200, json={
                "object": "database", "id": database, "url": f"https://notion.so/{database}",
                "data_sources": [{"id": source, "name": "Notes"}],
            })  # fmt: skip
        if path.startswith("/data_sources/") and path.endswith("/query"):
            source = path.split("/")[2]
            if source not in self.databases:
                return httpx2.Response(404, json={"message": "not shared"})
            rows = [(i, r) for i, r in self.databases[source].items() if not r["in_trash"]]
            start = int(body.get("start_cursor") or 0)
            size = body["page_size"]
            shown = rows[start : start + size]
            more = start + size < len(rows)
            return httpx2.Response(200, json={
                "results": [self.page(i, r) for i, r in shown], "has_more": more,
                "next_cursor": str(start + size) if more else None,
            })  # fmt: skip
        if path.startswith("/data_sources/") and request.method == "PATCH":
            self.added_columns.append(body["properties"])
            return httpx2.Response(200, json={"object": "data_source"})
        if path.startswith("/data_sources/"):
            return httpx2.Response(200, json={
                "object": "data_source", "url": "https://notion.so/ds",
                "parent": {"type": "database_id", "database_id": "db-1"},
                "properties": {"Name": {"type": "title"}, "Date": {"type": "date"}},
            })  # fmt: skip
        if path == "/pages" and request.method == "POST" and "page_id" in body["parent"]:
            if body["parent"]["page_id"] not in self.shared:
                return httpx2.Response(404, json={"message": "not shared"})
            ident = str(uuid.uuid4())
            self.shared.add(ident)  # a page under a shared page is shared too
            self.titles[ident] = body["properties"]["title"]["title"][0]["text"]["content"]
            self.parents[ident] = body["parent"]["page_id"]
            return httpx2.Response(200, json={"id": ident, "url": f"https://notion.so/{ident}"})
        if path.startswith("/pages/") and request.method == "GET":
            ident = path.split("/")[2]
            if ident not in self.shared:
                return httpx2.Response(404, json={"message": "not shared"})
            return httpx2.Response(200, json={
                "object": "page", "id": ident, "url": f"https://notion.so/{ident}",
                "properties": {"title": {"type": "title", "title": [{"plain_text": "Clients"}]}},
            })  # fmt: skip
        if path == "/pages" and request.method == "POST":
            source = body["parent"]["data_source_id"]
            if source not in self.databases:
                return httpx2.Response(404, json={"message": "not shared"})
            props = cast(dict[str, Any], body["properties"])
            ident = self.row(
                source, props["Note"]["title"][0]["text"]["content"],
                self.value(props, "Agent"), self.value(props, "Source task"),
            )  # fmt: skip
            children = cast(list[dict[str, Any]], body.get("children") or [])
            if children:
                self.bodies[ident] = "\n".join(
                    c["paragraph"]["rich_text"][0]["text"]["content"] for c in children
                )
            if "Kind" in props:
                self.kinds[ident] = props["Kind"]["select"]["name"]
            return httpx2.Response(200, json=self.page(ident, self.databases[source][ident]))
        if path.startswith("/pages/") and request.method == "PATCH":
            ident = path.split("/")[2]
            for rows_of in self.databases.values():
                if ident in rows_of:
                    rows_of[ident]["in_trash"] = body["in_trash"]
            return httpx2.Response(200, json={"id": ident})
        if path.startswith("/blocks/"):
            text = self.bodies.get(path.split("/")[2], "")
            blocks = [{"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": text}]}}]
            return httpx2.Response(200, json={"results": blocks if text else [],
                                              "has_more": False})  # fmt: skip
        if path == "/search":
            return httpx2.Response(200, json={"has_more": False, "results": [
                {"object": "page", "id": PARENT, "url": "https://notion.so/p",
                 "properties": {"title": {"type": "title", "title": [{"plain_text": "Clients"}]}}},
                {"object": "page", "id": "partial"},
            ]})  # fmt: skip
        return httpx2.Response(404, json={"message": f"no {path}"})


@pytest.fixture
def notion(database_url: str, monkeypatch: pytest.MonkeyPatch) -> FakeNotion:
    fake = FakeNotion()
    monkeypatch.setattr(notion_api, "transport", httpx2.MockTransport(fake.handle))
    memory._synced.clear()  # pyright: ignore[reportPrivateUsage]
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_project(session, "acme-blog", owner="acme")
        make_project(session, "globex-app", owner="globex")
    add_connection("acme-notion", "acme")
    add_connection("globex-notion", "globex")
    add_connection("team-notion", "you", shared=True)
    return fake


def add_connection(name: str, client: str, *, shared: bool = False) -> None:
    secret = json.dumps({"vendor": memory.VENDOR, "api_key": "ntn_fake"})
    with session_scope() as session:
        from ringkeeper.config import get_settings  # noqa: PLC0415 (as the tests' own settings)

        ref = secrets_in.store_connection(
            session, get_settings(), name, InstanceType.VENDOR, secret
        )
        instances.create_instance(session, InstanceIn(
            name=name, title=name, type=InstanceType.VENDOR, env=Env.PROD, secret_ref=ref,
            owner=client, config={"vendor": memory.VENDOR}, shared=shared,
        ))  # fmt: skip


def worker(project: str, agent: str | None = None, memory_of: str | None = None) -> Caller:
    """A caller as a worker's token makes one, in a task of its own."""
    with session_scope() as session:
        found = projects.get_project(session, project)
        ident = None
        if agent:
            row = Agent(project_id=found.id, name=agent, instructions="", memory=memory_of)
            session.add(row)
            session.flush()
            ident = row.id
        task = Task(project=project, prompt="p", status=TaskStatus.RUNNING, max_turns=5,
                    max_runtime_s=60, changed_files=[], auth_mode=AuthMode.API_KEY,
                    agent_id=ident)  # fmt: skip
        session.add(task)
        session.flush()
        row2 = WorkerSession(task_id=task.id, ordinal=1, role=SessionRole.WORKER,
                             status=TaskStatus.RUNNING, prompt="p",
                             state_volume="v")  # fmt: skip
        session.add(row2)
        session.flush()
        return Caller(project, str(row2.id), "token-1")


def to_notion(project: str, connection: str, parent: str = PARENT) -> str:
    with session_scope() as session:
        where = memory.attach(session, project, connection, parent=parent)
        memory.choose(session, project, memory.NOTION)
    return str(where["data_source"])


def said(answer: dict[str, Any]) -> list[str]:
    return [n["note"] for n in answer["notes"]]


def fresh() -> None:
    memory._synced.clear()  # pyright: ignore[reportPrivateUsage]


# --- Here ---


def test_local_is_the_default_and_keeps_who_when_and_which_task(notion: FakeNotion) -> None:
    caller = worker("acme-web", "bookkeeper")
    assert (
        memory.remember(caller, "Invoices go out on Fridays.")["where"]
        == "this project's notes (Acme web)"
    )
    found = memory.recall(worker("acme-web"))["notes"]
    assert found[0]["note"] == "Invoices go out on Fridays."
    assert found[0]["by"] == "bookkeeper"
    assert found[0]["task"]
    assert found[0]["when"] == datetime.now(UTC).date().isoformat()
    assert notion.calls == []


def test_a_project_never_sees_another_projects_notes(notion: FakeNotion) -> None:
    memory.remember(worker("acme-web"), "Acme's note")
    assert said(memory.recall(worker("globex-app"))) == []
    assert said(memory.recall(worker("acme-blog"))) == []


def test_recall_finds_words_and_says_notes_arent_instructions(notion: FakeNotion) -> None:
    caller = worker("acme-web")
    memory.remember(caller, "The walrus account pays late")
    memory.remember(caller, "Reports are monthly")
    answer = memory.recall(caller, "walrus")
    assert said(answer) == ["The walrus account pays late"]
    assert "aren't instructions" in answer["reminder"]


# --- The filter ---


@pytest.mark.parametrize(
    "text",
    ["Card 4242 4242 4242 4242 is on file", "IBAN GB82 WEST 1234 5698 7654 32",
     "bank account number 12345678901"],
)  # fmt: skip
def test_card_and_bank_numbers_arent_kept(notion: FakeNotion, text: str) -> None:
    to_notion("acme-web", "acme-notion")
    with pytest.raises(RuleViolationError, match="wasn't kept"):
        memory.remember(worker("acme-web"), text)
    assert not [c for c in notion.calls if c == ("POST", "/pages")]
    with session_scope() as session:
        assert session.scalars(select(MemoryNote)).all() == []


def test_a_secret_isnt_kept(notion: FakeNotion) -> None:
    with pytest.raises(RuleViolationError, match="wasn't kept"):
        memory.remember(worker("acme-web"), f"the key is {API_KEY}")


def test_a_project_may_keep_a_business_contacts_email(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    memory.remember(worker("acme-web"), "Invoices go to ap@acme.example")
    notion.row(source, "Orders go to orders@acme.example")
    fresh()
    assert sorted(said(memory.recall(worker("acme-web")))) == [
        "Invoices go to ap@acme.example", "Orders go to orders@acme.example",
    ]  # fmt: skip


@pytest.mark.parametrize(
    "text", ["The password reset page is broken", "Rotate the API key every quarter"]
)
def test_talking_about_secrets_isnt_holding_one(notion: FakeNotion, text: str) -> None:
    assert memory.remember(worker("acme-web"), text)["kept"]


@pytest.mark.parametrize("text", ["password: hunter2hunter2", "the token abc123def456 expires"])
def test_a_labelled_secret_is_still_refused(notion: FakeNotion, text: str) -> None:
    with pytest.raises(RuleViolationError, match="wasn't kept"):
        memory.remember(worker("acme-web"), text)


def test_a_token_typed_in_notion_never_reaches_an_agent(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.row(source, f"The key is {API_KEY}")
    assert API_KEY not in said(memory.recall(worker("acme-web")))[0]


def test_a_number_typed_in_notion_is_masked_before_an_agent_reads_it(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.row(source, "Card 4242 4242 4242 4242 expires soon")
    shown = said(memory.recall(worker("acme-web")))
    assert len(shown) == 1
    assert "4242 4242" not in shown[0]


def test_a_note_too_long_is_refused(notion: FakeNotion) -> None:
    with pytest.raises(RuleViolationError, match="at most"):
        memory.remember(worker("acme-web"), "x" * (memory.MOST_CHARS + 1))


# --- Notion: writing and reading ---


def test_attaching_makes_one_database_named_for_the_project(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    assert notion.titles[source].endswith("Ringkeeper memory")
    assert notion.titles[source].count(chr(0x203A)) == 2
    again = to_notion("acme-web", "acme-notion")  # the same page: the same database
    assert again == source
    assert len(notion.databases) == 1


def test_a_project_note_in_notion_is_never_called_personal(notion: FakeNotion) -> None:
    """From a real run: remember said {"where": "Notion"} for Bayside's own note, the worker
    took it for the owner's personal memory, and asked for it to be deleted by hand. The
    answer, the audit line and the task's page say whose notes it went to."""
    to_notion("acme-web", "acme-notion")
    caller = worker("acme-web", "bookkeeper")
    where = memory.remember(caller, "Invoices live in the Billing base")["where"]
    assert where == "this project's notes (Acme web, in Notion)"
    with session_scope() as session:
        audit = (
            session.scalars(select(AuditEvent).where(AuditEvent.tool == "remember"))
            .all()[-1]
            .reason
        )
        task = memory._worker(session, caller.session_id).task_id  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
        shown = memory.saved_by(session, task)
    assert audit == f"kept a note in {where}"
    assert audit is not None
    assert shown == [{"note": "Invoices live in the Billing base", "where": where}]
    for said_where in (where, audit, shown[0]["where"]):
        assert "personal" not in said_where.lower()
        assert memory.MY_MEMORY not in said_where


LONG = (
    "Bayside's workflows in agency-n8n are named 'Bayside #N' and 'Mr. Gutter #N', and the "
    "older ones still carry the 2024 prefix. Filter by name rather than listing them all."
)


def test_a_note_in_notion_has_a_short_title_a_kind_and_its_whole_text_in_the_page(
    notion: FakeNotion,
) -> None:
    """From a real note: the title was the whole text, Agent was empty and Source task was a
    bare ID."""
    source = to_notion("acme-web", "acme-notion")
    caller = worker("acme-web")
    memory.remember(caller, LONG, kind="Where things live")
    ((ident, row),) = notion.databases[source].items()
    assert len(row["text"]) <= notion_api.TITLE_CHARS
    assert row["text"].endswith("…")
    assert LONG.startswith(row["text"].rstrip("…"))
    assert notion.bodies[ident] == LONG  # the whole note, in the page
    assert notion.kinds[ident] == "Where things live"
    assert row["agent"] == "Ringkeeper"  # no named agent ran
    with session_scope() as session:
        task = memory._worker(session, caller.session_id).task_id  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    assert row["task"].endswith(f"({task})")
    assert len(row["task"]) > len(str(task)) + 2  # the request's first words come first
    fresh()
    assert said(memory.recall(worker("acme-web"))) == [LONG]  # read back once, not twice
    with session_scope() as session:  # and still listed under the task that saved it
        assert [n["note"] for n in memory.saved_by(session, task)] == [LONG]


def test_a_row_typed_by_hand_with_only_a_title_still_reads(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.row(source, "Invoices are approved by Dana")
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Invoices are approved by Dana"]


def test_an_older_database_gets_the_kind_column_and_keeps_its_rows(notion: FakeNotion) -> None:
    place = notion_api.use_place("ntn_fake", "older-source")
    assert notion.added_columns == [{"Kind": {"select": {"options": [
        {"name": k} for k in notion_api.KINDS
    ]}}}]  # fmt: skip
    assert place.columns["kind"] == "Kind"
    assert not any(method == "PATCH" and path.startswith("/pages/")
                   for method, path in notion.calls)  # fmt: skip


def test_a_note_that_says_what_one_already_says_is_refused(notion: FakeNotion) -> None:
    memory.remember(worker("acme-web"), "Invoices live in the Billing base, table Invoices.")
    with pytest.raises(RuleViolationError, match="already noted"):
        memory.remember(worker("acme-web"), "Invoices live in the Billing base, table Invoices")
    with pytest.raises(RuleViolationError, match="already noted"):
        memory.keep("acme-web", "invoices live in the billing base (table Invoices)")
    memory.remember(worker("acme-web"), "Invoices are approved by Dana before they go out.")
    assert len(said(memory.recall(worker("acme-web")))) == 2


def test_workers_are_told_what_makes_a_good_note() -> None:
    told = " ".join(memory.PROJECT_MEMORY.split())
    assert "One fact per note, each understandable on its own" in told
    assert "Never save counts, dates, statuses or anything else that changes" in told
    assert "never how a tool works in general (paging, limits per call)" in told
    assert "only when it blocks a kind of request" in told
    assert "named 'Bayside #N' and 'Mr. Gutter #N'. Filter by name." in told  # a good one
    assert "n8n has 184 workflows" in told  # a bad one
    assert "already noted" in told


def test_a_note_goes_to_the_projects_database_with_agent_and_task(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    answer = memory.remember(worker("acme-web", "bookkeeper"), "Pays by check")
    assert answer["where"] == "this project's notes (Acme web, in Notion)"
    (row,) = notion.databases[source].values()
    assert (row["text"], row["agent"]) == ("Pays by check", "bookkeeper")
    assert row["task"]


def test_its_own_note_isnt_read_back_from_notion_needlessly(notion: FakeNotion) -> None:
    to_notion("acme-web", "acme-notion")
    caller = worker("acme-web")
    memory.remember(caller, "One")
    memory.recall(caller)
    assert not [c for c in notion.calls if c[1].startswith("/blocks/")]


def test_a_failed_write_says_so_and_keeps_no_local_copy(notion: FakeNotion) -> None:
    to_notion("acme-web", "acme-notion")
    notion.down = True
    with pytest.raises(notion_api.NotionError):
        memory.remember(worker("acme-web"), "Lost")
    with session_scope() as session:
        assert session.scalars(select(MemoryNote)).all() == []


# --- Notion: both ways ---


def test_a_row_someone_adds_is_picked_up_on_the_next_run(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    assert said(memory.recall(worker("acme-web"))) == []
    fresh()
    notion.row(source, "Added by hand in Notion")
    found = memory.recall(worker("acme-web"))["notes"]
    assert [n["note"] for n in found] == ["Added by hand in Notion"]
    assert found[0]["by"] == "a person"


def test_what_another_tool_wrote_inside_the_page_is_read_too(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    ident = notion.row(source, "From Claude")
    notion.bodies[ident] = "The longer story, inside the page."
    assert said(memory.recall(worker("acme-web"))) == [
        "From Claude\nThe longer story, inside the page."
    ]


def test_an_edit_in_notion_wins(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    caller = worker("acme-web")
    memory.remember(caller, "Monthly")
    (ident,) = notion.databases[source]
    notion.edit(source, ident, "Weekly, since November")
    fresh()
    assert said(memory.recall(caller)) == ["Weekly, since November"]


def test_an_edit_within_the_same_minute_still_wins(notion: FakeNotion) -> None:
    """Notion's edit times can stop at the minute."""
    source = to_notion("acme-web", "acme-notion")
    ident = notion.row(source, "First")
    memory.recall(worker("acme-web"))
    notion.databases[source][ident]["text"] = "Second"  # the same edit time
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Second"]


def test_a_deleted_row_is_forgotten(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    keep, gone = notion.row(source, "Keep"), notion.row(source, "Gone")
    assert sorted(said(memory.recall(worker("acme-web")))) == ["Gone", "Keep"]
    notion.databases[source][gone]["in_trash"] = True
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Keep"]
    assert keep


def test_when_notion_is_down_nothing_is_forgotten_and_it_says_so(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.row(source, "Still here")
    memory.recall(worker("acme-web"))
    fresh()
    notion.down = True
    answer = memory.recall(worker("acme-web"))
    assert said(answer) == ["Still here"]
    assert "out of date" in answer["warning"]


def test_a_long_list_is_read_to_its_end_before_anything_is_forgotten(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    for n in range(250):
        notion.row(source, f"Note {n}")
    pulled = memory.sync("acme-web")
    assert pulled.added == 250
    assert memory.sync("acme-web").forgotten == 0


# --- Isolation ---


def test_a_clients_connection_cant_hold_another_clients_memory(notion: FakeNotion) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="can't hold"):
        memory.attach(session, "globex-app", "acme-notion", parent=PARENT)


def test_a_clients_page_is_never_attached_to_another_clients_project(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "team-notion")
    with session_scope() as session, pytest.raises(RuleViolationError, match="another client"):
        memory.attach(session, "globex-app", "team-notion", parent=PARENT)
    with session_scope() as session, pytest.raises(RuleViolationError, match="another client"):
        memory.attach(session, "globex-app", "team-notion", data_source=source)


def test_two_clients_notes_stay_apart_in_notion(notion: FakeNotion) -> None:
    acme = to_notion("acme-web", "acme-notion", PARENT)
    globex = to_notion("globex-app", "globex-notion", OTHER_PARENT)
    assert acme != globex
    memory.remember(worker("acme-web"), "Acme only")
    memory.remember(worker("globex-app"), "Globex only")
    assert [r["text"] for r in notion.databases[acme].values()] == ["Acme only"]
    assert [r["text"] for r in notion.databases[globex].values()] == ["Globex only"]
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Acme only"]
    assert said(memory.recall(worker("globex-app"))) == ["Globex only"]


def test_a_page_not_shared_with_the_connection_says_how_to_share_it(notion: FakeNotion) -> None:
    with session_scope() as session, pytest.raises(notion_api.NotionError, match="Connections"):
        memory.attach(session, "acme-web", "acme-notion", parent=OUTSIDE)


# --- An agent's own choice ---


def test_an_agent_can_keep_its_notes_here_while_the_project_uses_notion(
    notion: FakeNotion,
) -> None:
    source = to_notion("acme-web", "acme-notion")
    assert (
        memory.remember(worker("acme-web", "private", "local"), "Mine")["where"]
        == "this project's notes (Acme web)"
    )
    assert notion.databases[source] == {}
    assert (
        memory.remember(worker("acme-web"), "Everyone's")["where"]
        == "this project's notes (Acme web, in Notion)"
    )
    assert said(memory.recall(worker("acme-web"))) == ["Everyone's"]


def test_an_agent_can_use_notion_while_the_project_stays_here(notion: FakeNotion) -> None:
    with session_scope() as session:
        memory.attach(session, "acme-web", "acme-notion", parent=PARENT)
    assert (
        memory.remember(worker("acme-web", "writer", "notion"), "Out")["where"]
        == "this project's notes (Acme web, in Notion)"
    )
    assert memory.remember(worker("acme-web"), "Here")["where"] == "this project's notes (Acme web)"


# --- Shared notes ---


def share(*, agents_write: bool = False) -> str:
    with session_scope() as session:
        where = memory.attach_shared(
            session, "team-notion", parent=OTHER_PARENT, agents_write=agents_write
        )
    return str(where["data_source"])


def test_shared_notes_need_a_shared_connection(notion: FakeNotion) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="isn't a shared"):
        memory.attach_shared(session, "acme-notion", parent=OTHER_PARENT)


def test_shared_notes_reach_every_project_read_only(notion: FakeNotion) -> None:
    source = share()
    notion.row(source, "How I price retainers")
    for project in ("acme-web", "globex-app"):
        fresh()
        found = memory.recall(worker(project))["notes"]
        assert [(n["note"], n["where"]) for n in found] == [
            ("How I price retainers", "shared notes")
        ]
    with pytest.raises(RuleViolationError, match="read-only"):
        memory.remember(worker("acme-web"), "Not allowed", shared=True)
    assert len(notion.databases[source]) == 1


def test_the_owner_can_let_agents_write_shared_notes(notion: FakeNotion) -> None:
    source = share(agents_write=True)
    assert memory.remember(worker("acme-web"), "Useful", shared=True)["where"] == "shared notes"
    assert len(notion.databases[source]) == 1


def test_a_project_can_leave_shared_notes_out(notion: FakeNotion) -> None:
    source = share()
    notion.row(source, "Mine")
    with session_scope() as session:
        memory.choose(session, "acme-web", memory.LOCAL, shared_notes=False)
    assert said(memory.recall(worker("acme-web"))) == []


# --- My memory ---


def mine() -> str:
    with session_scope() as session:
        where = memory.attach_mine(session, "team-notion", parent=OTHER_PARENT)
    return str(where["data_source"])


def test_my_memory_makes_its_database_and_agents_add_to_it(notion: FakeNotion) -> None:
    source = mine()
    assert notion.titles[source] == "My memory"
    assert memory.remember(worker("acme-web"), "I like short emails", mine=True)["where"] == (
        "My memory"
    )
    assert [r["text"] for r in notion.databases[source].values()] == ["I like short emails"]


def test_every_project_finds_my_memory(notion: FakeNotion) -> None:
    source = mine()
    notion.row(source, "I write in plain words")
    for project in ("acme-web", "globex-app"):
        fresh()
        found = memory.recall(worker(project))["notes"]
        assert [(n["note"], n["where"]) for n in found] == [("I write in plain words", "my memory")]


def test_my_memory_needs_a_shared_connection(notion: FakeNotion) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="isn't a shared"):
        memory.attach_mine(session, "acme-notion", parent=OTHER_PARENT)


def test_my_memory_and_shared_notes_stay_apart(notion: FakeNotion) -> None:
    notes = share()
    source = mine()
    assert notes != source
    notion.row(notes, "From the shared page")
    notion.row(source, "From my memory")
    found = {n["note"]: n["where"] for n in memory.recall(worker("acme-web"))["notes"]}
    assert found == {"From the shared page": "shared notes", "From my memory": "my memory"}
    with session_scope() as session:
        memory.detach_shared(session)
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["From my memory"]


def test_a_save_naming_a_client_is_refused_and_recorded(notion: FakeNotion) -> None:
    source = mine()
    with pytest.raises(RuleViolationError, match="only for the owner's own"):
        memory.remember(worker("acme-web"), "Acme likes weekly calls", mine=True)
    assert notion.databases[source] == {}


@pytest.mark.parametrize(
    ("text", "what"),
    [
        ("Reach me at pat@elsewhere.example for urgent work", "an email address"),
        ("Call +1 415 555 0132 if it's urgent", "a phone number"),
        ("My office is 1200 Market Street, Suite 40", "a street address"),
    ],
)
def test_details_are_taken_out_and_the_rest_is_kept(
    notion: FakeNotion, text: str, what: str
) -> None:
    source = mine()
    answer = memory.remember(worker("acme-web"), text, mine=True)
    assert answer["removed"] == [what]
    (row,) = notion.databases[source].values()
    assert "[removed]" in row["text"]


def test_details_typed_in_my_memory_never_reach_an_agent(notion: FakeNotion) -> None:
    source = mine()
    notion.row(source, "Reach me at pat@elsewhere.example or 415-555-0132")
    (shown,) = said(memory.recall(worker("acme-web")))
    assert "elsewhere" not in shown
    assert "0132" not in shown


def test_shared_notes_keep_the_same_rules(notion: FakeNotion) -> None:
    share(agents_write=True)
    with pytest.raises(RuleViolationError, match="only for the owner's own"):
        memory.remember(worker("acme-web"), "Globex pays late", shared=True)
    assert memory.remember(worker("acme-web"), "Mail me at a@b.example", shared=True)["removed"]


def test_a_worker_is_told_about_my_memory_only_when_its_set_up(notion: FakeNotion) -> None:
    with session_scope() as session:
        assert memory.hints(session, projects.get_project(session, "acme-web"), None) == []
    mine()
    with session_scope() as session:
        (said_to,) = memory.hints(session, projects.get_project(session, "acme-web"), None)
    assert "mine set to true" in said_to
    assert "Never put anything about a client" in said_to
    with session_scope() as session:  # Check my setup keeps nothing: not told to save any
        found = projects.get_project(session, "acme-web")
        assert memory.hints(session, found, None, saving=False) == []


def test_a_project_can_leave_my_memory_out(notion: FakeNotion) -> None:
    source = mine()
    notion.row(source, "Mine")
    with session_scope() as session:
        memory.choose(session, "acme-web", memory.LOCAL, shared_notes=False)
        assert memory.hints(session, projects.get_project(session, "acme-web"), None) == []
    assert said(memory.recall(worker("acme-web"))) == []
    with pytest.raises(RuleViolationError, match="isn't set up"):
        memory.remember(worker("acme-web"), "Nope", mine=True)


def test_my_memory_through_the_api(owner: TestClient, notion: FakeNotion) -> None:
    shown = owner.get("/memory/mine").json()
    assert shown["mine"] is None
    assert [c["name"] for c in shown["connections"]] == ["team-notion"]
    saved = owner.put("/memory/mine", json={"connection": "team-notion", "parent": OTHER_PARENT})
    assert saved.status_code == 200, saved.text
    assert saved.json()["mine"]["connection"] == "team-notion"
    assert owner.get("/projects/acme-web/memory").json()["shared_available"] is True
    assert owner.delete("/memory/mine").status_code == 204
    assert owner.get("/memory/mine").json()["mine"] is None


# --- Through the API ---


def test_settings_notes_and_delete_through_the_api(owner: TestClient, notion: FakeNotion) -> None:
    status = owner.get("/projects/acme-web/memory").json()
    assert status["backend"] == "local"
    assert {c["name"] for c in status["connections"]} == {"acme-notion", "team-notion"}
    pages = owner.get("/notion/acme-notion/pages").json()
    assert [p["title"] for p in pages] == ["Clients"]  # the partial answer is left out

    refused = owner.put("/projects/acme-web/memory", json={"backend": "notion"})
    assert refused.status_code in (409, 422)
    saved = owner.put("/projects/acme-web/memory", json={
        "backend": "notion", "connection": "acme-notion", "parent": PARENT,
    })  # fmt: skip
    assert saved.status_code == 200, saved.text
    assert saved.json()["backend"] == "notion"
    assert "ntn_fake" not in saved.text

    memory.remember(worker("acme-web"), "Through the API")
    listed = owner.get("/projects/acme-web/memory/notes")
    assert listed.headers["X-Total-Count"] == "1"
    note = listed.json()[0]
    assert owner.delete(f"/projects/acme-web/memory/notes/{note['id']}").status_code == 204
    (source,) = notion.databases
    assert [r["in_trash"] for r in notion.databases[source].values()] == [True]
    assert owner.get("/projects/acme-web/memory/notes").json() == []


def test_a_note_of_another_project_cant_be_deleted_through_this_one(
    owner: TestClient, notion: FakeNotion
) -> None:
    memory.remember(worker("globex-app"), "Globex's")
    note = owner.get("/projects/globex-app/memory/notes").json()[0]
    refused = owner.delete(f"/projects/acme-web/memory/notes/{note['id']}")
    assert refused.status_code in (404, 409, 422)
    assert len(owner.get("/projects/globex-app/memory/notes").json()) == 1


def test_syncing_by_hand_says_what_changed(owner: TestClient, notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.row(source, "New")
    assert owner.post("/projects/acme-web/memory/sync").json() == {
        "added": 1, "changed": 0, "forgotten": 0,
    }  # fmt: skip


def test_an_agent_has_no_way_to_name_a_place_or_delete() -> None:
    """The tools' whole surface: nothing that says where, and no delete."""
    import inspect  # noqa: PLC0415

    assert list(inspect.signature(memory.recall).parameters) == ["caller", "words", "limit"]
    assert list(inspect.signature(memory.remember).parameters) == [
        "caller", "text", "shared", "mine", "kind",
    ]  # fmt: skip


def test_the_memory_token_never_reaches_a_note_or_the_vault_log(notion: FakeNotion) -> None:
    assert get_vault() is not None
    to_notion("acme-web", "acme-notion")
    memory.remember(worker("acme-web"), "Plain")
    with session_scope() as session:
        assert all("ntn_" not in n.body for n in session.scalars(select(MemoryNote)))


# --- A Notion memory connection is never a project's tool ---


def test_notion_memory_cant_be_linked_or_given_to_every_project(notion: FakeNotion) -> None:
    from ringkeeper.schemas import BindingIn  # noqa: PLC0415
    from ringkeeper.services import bindings  # noqa: PLC0415

    with session_scope() as session, pytest.raises(RuleViolationError, match="holds agents"):
        bindings.set_binding(session, BindingIn(project="acme-web", instance="acme-notion"))
    with session_scope() as session:
        team = instances.get_instance(session, "team-notion")
        data = InstanceIn.model_validate(team, from_attributes=True)
        instances.update_instance(session, data.model_copy(update={"all_projects": True}))
    with session_scope() as session:
        assert instances.get_instance(session, "team-notion").all_projects is False
        assert all(not p.bindings for p in projects.list_projects(session))


def test_a_worker_cant_call_notion_memory(notion: FakeNotion) -> None:
    from ringkeeper.adapters import ADAPTERS  # noqa: PLC0415
    from ringkeeper.calls import ToolCall  # noqa: PLC0415
    from ringkeeper.models import ProjectInstance  # noqa: PLC0415
    from ringkeeper.services import connection_note, onboarding  # noqa: PLC0415
    from ringkeeper.services.calls import RefusalError, authorize  # noqa: PLC0415

    with session_scope() as session:  # linked before this change, say
        project = projects.get_project(session, "acme-web")
        notes = instances.get_instance(session, "acme-notion")
        session.add(ProjectInstance(project=project, instance=notes))
    vendor_ = InstanceType.VENDOR
    call = ToolCall(Caller("acme-web", "s", "t"), vendor_, "call_operation", "acme-notion",
                    {"operation": "whoami"})  # fmt: skip
    with session_scope() as session:
        with pytest.raises(RefusalError, match="holds agents' notes"):
            authorize(session, call, ADAPTERS[vendor_].tool("call_operation"))
        assert connection_note.note(projects.get_project(session, "acme-web")) is None
        assert "acme-notion" not in [i.name for i in onboarding.unlinked(session)]
        tried = onboarding.suggestions(session, get_test_settings())
        assert all("notion" not in s.lower() for s in tried)


def get_test_settings() -> Any:
    from ringkeeper.config import get_settings  # noqa: PLC0415

    return get_settings()


def test_links_made_before_go_away(database_url: str, notion: FakeNotion) -> None:
    """0039: a Notion memory connection's links and "every project" go; the rest stay."""
    from ringkeeper import migrate  # noqa: PLC0415
    from ringkeeper.db import dispose_engines  # noqa: PLC0415
    from ringkeeper.models import Instance, ProjectInstance  # noqa: PLC0415
    from tests.helpers import make_instance  # noqa: PLC0415

    with session_scope() as session:
        make_instance(session, "acme-db", owner="acme")
        session.flush()
        web = projects.get_project(session, "acme-web")
        for name in ("acme-notion", "acme-db"):
            session.add(
                ProjectInstance(project=web, instance=instances.get_instance(session, name))
            )
        instances.get_instance(session, "team-notion").all_projects = True
    migrate.downgrade(database_url, "0038")
    dispose_engines()
    migrate.upgrade(database_url)
    dispose_engines()
    with session_scope() as session:
        linked = {b.instance.name for b in projects.get_project(session, "acme-web").bindings}
        assert linked == {"acme-db"}
        team = session.scalars(select(Instance).where(Instance.name == "team-notion")).one()
        assert team.all_projects is False


# --- Notion for every project ---


def test_every_project_gets_a_database_under_its_clients_page(notion: FakeNotion) -> None:
    kept = to_notion("acme-blog", "acme-notion", OTHER_PARENT)  # set up before: keeps its place
    with session_scope() as session:
        done = memory.use_everywhere(session, "team-notion", PARENT)
    assert sorted(done["set_up"]) == ["acme-web", "globex-app"]
    assert done["failed"] == {}
    with session_scope() as session:
        places = {p.name: memory.settings_of(p) for p in projects.list_projects(session)}
    assert all(s["backend"] == memory.NOTION for s in places.values())
    assert places["acme-blog"]["notion"]["data_source"] == kept
    pages = {notion.titles[p]: p for p, parent in notion.parents.items() if parent == PARENT}
    assert sorted(pages) == ["acme › Ringkeeper", "globex › Ringkeeper"]  # noqa: RUF001
    acme, globex = (places[n]["notion"]["data_source"] for n in ("acme-web", "globex-app"))
    assert notion.parents[acme] == pages["acme › Ringkeeper"]  # noqa: RUF001
    assert notion.parents[globex] == pages["globex › Ringkeeper"]  # noqa: RUF001
    memory.remember(worker("globex-app"), "Globex invoices in euros")
    assert said(memory.recall(worker("acme-web"))) == []


def test_a_new_project_follows_and_reuses_its_clients_page(notion: FakeNotion) -> None:
    with session_scope() as session:
        memory.use_everywhere(session, "team-notion", PARENT)
        make_project(session, "acme-shop", owner="acme")
    memory.follow_new("acme-shop")
    with session_scope() as session:
        shop = memory.settings_of(projects.get_project(session, "acme-shop"))
        web = memory.settings_of(projects.get_project(session, "acme-web"))
    assert shop["backend"] == memory.NOTION
    assert shop["notion"]["parent"] == web["notion"]["parent"]
    assert sum(parent == PARENT for parent in notion.parents.values()) == 2  # one per client


def test_every_project_needs_your_own_connection_and_a_reachable_page(
    notion: FakeNotion,
) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="shared Notion"):
        memory.use_everywhere(session, "acme-notion", PARENT)
    with session_scope() as session, pytest.raises(notion_api.NotionError):
        memory.use_everywhere(session, "team-notion", OUTSIDE)
    with session_scope() as session:
        assert memory.every_settings(session) == {}


def test_when_notion_is_down_a_task_still_starts(notion: FakeNotion) -> None:
    with session_scope() as session:
        memory.use_everywhere(session, "team-notion", PARENT)
        make_project(session, "acme-shop", owner="acme")
    notion.down = True
    memory.follow_new("acme-shop")  # doesn't raise: notes stay here this time
    with session_scope() as session:
        assert memory.settings_of(projects.get_project(session, "acme-shop")).get("backend") is None


def test_every_project_through_the_api(owner: TestClient, notion: FakeNotion) -> None:
    shown = owner.get("/memory/every").json()
    assert shown["every"] is None
    assert [c["name"] for c in shown["connections"]] == ["team-notion"]
    saved = owner.put("/memory/every", json={"connection": "team-notion", "root": PARENT})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["every"]["title"] == "Clients"
    assert {p["where"] for p in body["projects"]} == {"notion"}
    assert owner.get("/projects/acme-web/memory").json()["follows"] is False
    assert owner.delete("/memory/every").status_code == 204
    assert owner.get("/memory/every").json()["every"] is None
    assert owner.get("/projects/acme-web/memory").json()["backend"] == "notion"  # kept


# --- Workers save, and start from the latest notes ---


def age(note: str, minutes: int) -> None:
    """Notes made in one test share a clock tick: set one's time so the order is known."""
    from datetime import timedelta  # noqa: PLC0415

    with session_scope() as session:
        row = session.scalars(select(MemoryNote).where(MemoryNote.body.startswith(note))).one()
        row.created_at = datetime.now(UTC) - timedelta(minutes=minutes)


def block(project: str, *, saving: bool = True) -> str:
    with session_scope() as session:
        return memory.project_block(session, projects.get_project(session, project), None,
                                    saving=saving)  # fmt: skip


def test_a_worker_is_told_what_to_save_and_gets_the_latest_notes(notion: FakeNotion) -> None:
    memory.remember(worker("acme-web"), "Invoices live in the Billing base, table Invoices")
    memory.remember(worker("acme-web"), "Acme wants totals in USD")
    memory.remember(worker("globex-app"), "Globex's own note")
    age("Invoices live", 10)
    told = block("acme-web")
    assert "PROJECT MEMORY" in told
    assert "where things live" in told
    assert "Don't save the task's result" in told
    assert "only if you saved a note" in told
    assert memory.READ_AS_NOTES in told
    newest, older = told.index("totals in USD"), told.index("Billing base")
    assert newest < older
    assert "Globex" not in told
    assert "PROJECT MEMORY" not in block("acme-web", saving=False)  # Check my setup only reads
    assert "Billing base" in block("acme-web", saving=False)


def test_the_latest_notes_are_capped(notion: FakeNotion) -> None:
    for n in range(30):
        memory.remember(worker("acme-web"), f"Note {n}: " + "x" * 400)
        age(f"Note {n}:", 100 - n)
    told = block("acme-web")
    notes = told.split(memory.LATEST, 1)[1]
    assert len(notes) <= memory.LATEST_CHARS + 200
    assert "Note 29" in notes
    assert "Note 0:" not in notes


def test_no_notes_yet_means_only_what_to_save(notion: FakeNotion) -> None:
    told = block("acme-blog")
    assert "PROJECT MEMORY" in told
    assert memory.LATEST not in told


def test_the_owner_adds_a_note_to_the_projects_own_place(notion: FakeNotion) -> None:
    to_notion("acme-web", "acme-notion")
    kept = memory.keep("acme-web", "Acme's books close on the 5th")
    assert kept == {
        "kept": True,
        "where": "this project's notes (Acme web, in Notion)",
        "note": "Acme's books close on the 5th",
    }
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Acme's books close on the 5th"]
    assert memory.recall(worker("acme-web"))["notes"][0]["by"] == "a person"
    with pytest.raises(RuleViolationError, match="wasn't kept"):
        memory.keep("acme-web", "Card 4242 4242 4242 4242 is on file")
    assert memory.keep("acme-blog", "Kept here")["where"] == "this project's notes (Acme blog)"


def test_a_task_page_shows_what_it_saved(owner: TestClient, notion: FakeNotion) -> None:
    caller = worker("acme-web")
    memory.remember(caller, "The Billing base is appXYZ")
    with session_scope() as session:
        task = str(memory._worker(session, caller.session_id).task_id)  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
    assert owner.get(f"/tasks/{task}/memory").json() == [
        {"note": "The Billing base is appXYZ", "where": "this project's notes (Acme web)"}
    ]
    added = owner.post("/projects/acme-web/memory/notes",
                       json={"note": "Remember the Friday cutoff", "task": task})  # fmt: skip
    assert added.status_code == 201, added.text
    assert len(owner.get(f"/tasks/{task}/memory").json()) == 2
    secret = "token sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789"
    refused = owner.post("/projects/acme-web/memory/notes", json={"note": secret})
    assert refused.status_code == 409


# --- "Remember that ..." in a conversation ---


@pytest.mark.parametrize(
    ("body", "note"),
    [
        ("Remember that Acme's invoices go out on Fridays", "Acme's invoices go out on Fridays"),
        ("please remember: the staging base is appABC", "The staging base is appABC"),
        ("Remember that file I sent?", None),
        ("Remember to send the report on Monday", None),
        ("What do you remember about Acme?", None),
    ],
)
def test_what_counts_as_remember_that(body: str, note: str | None) -> None:
    from ringkeeper.router import remember_now  # noqa: PLC0415

    assert remember_now.note_in(body) == note


def post(body: str, target: str | None = None) -> uuid.UUID:
    from ringkeeper.services import threads  # noqa: PLC0415

    with session_scope() as session:
        message = threads.post_message(session, body, author="owner", source="web")
        message.target_project = target
        return message.id


def test_remember_that_with_a_clear_project_is_kept_without_a_task(notion: FakeNotion) -> None:
    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.models import Message  # noqa: PLC0415
    from ringkeeper.router.service import route_next  # noqa: PLC0415
    from tests.fake_router import FakeModel  # noqa: PLC0415

    ident = post("Remember that Acme's invoices go out on Fridays", target="acme-web")
    route_next(get_settings(), FakeModel([]))  # no model call: it would have nothing to say
    with session_scope() as session:
        message = session.get_one(Message, ident)
        assert message.routed_at is not None
        assert message.routing
        assert message.routing["remembered"] == "acme-web"
        reply = message.routing["answer"]
        assert session.scalars(select(Task).where(Task.message_id == ident)).all() == []
    assert reply.startswith("Saved to ")
    assert "here in Ringkeeper" in reply
    assert said(memory.recall(worker("acme-web"))) == ["Acme's invoices go out on Fridays"]


def test_remember_that_without_a_clear_project_is_routed_as_usual(notion: FakeNotion) -> None:
    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.router.service import route_next  # noqa: PLC0415
    from tests.fake_router import FakeModel, output, routed  # noqa: PLC0415

    post("Remember that reports are monthly")  # three projects, none named
    model = FakeModel([output(routed("acme-web"))])
    route_next(get_settings(), model)
    assert model.calls  # the model routed it
    assert said(memory.recall(worker("acme-web"))) == []


def test_remember_that_never_keeps_a_secret(notion: FakeNotion) -> None:
    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.router.service import route_next  # noqa: PLC0415
    from tests.fake_router import FakeModel  # noqa: PLC0415

    post("Remember that the card is 4242 4242 4242 4242", target="acme-web")
    route_next(get_settings(), FakeModel([]))
    assert all("4242" not in n for n in said(memory.recall(worker("acme-web"))))
