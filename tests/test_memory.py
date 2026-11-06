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
        self.bodies: dict[str, str] = {}
        self.calls: list[tuple[str, str]] = []
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
        if path.startswith("/data_sources/"):
            return httpx2.Response(200, json={
                "object": "data_source", "url": "https://notion.so/ds",
                "parent": {"type": "database_id", "database_id": "db-1"},
                "properties": {"Name": {"type": "title"}, "Date": {"type": "date"}},
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
    assert memory.remember(caller, "Invoices go out on Fridays.")["where"] == "Ringkeeper"
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


def test_an_email_address_isnt_kept(notion: FakeNotion) -> None:
    with pytest.raises(RuleViolationError, match="personal details"):
        memory.remember(worker("acme-web"), "Write to pat@acme.example")


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


def test_a_note_goes_to_the_projects_database_with_agent_and_task(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    answer = memory.remember(worker("acme-web", "bookkeeper"), "Pays by check")
    assert answer["where"] == "Notion"
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
    assert memory.remember(worker("acme-web", "private", "local"), "Mine")["where"] == "Ringkeeper"
    assert notion.databases[source] == {}
    assert memory.remember(worker("acme-web"), "Everyone's")["where"] == "Notion"
    assert said(memory.recall(worker("acme-web"))) == ["Everyone's"]


def test_an_agent_can_use_notion_while_the_project_stays_here(notion: FakeNotion) -> None:
    with session_scope() as session:
        memory.attach(session, "acme-web", "acme-notion", parent=PARENT)
    assert memory.remember(worker("acme-web", "writer", "notion"), "Out")["where"] == "Notion"
    assert memory.remember(worker("acme-web"), "Here")["where"] == "Ringkeeper"


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
    assert list(inspect.signature(memory.remember).parameters) == ["caller", "text", "shared"]


def test_the_memory_token_never_reaches_a_note_or_the_vault_log(notion: FakeNotion) -> None:
    assert get_vault() is not None
    to_notion("acme-web", "acme-notion")
    memory.remember(worker("acme-web"), "Plain")
    with session_scope() as session:
        assert all("ntn_" not in n.body for n in session.scalars(select(MemoryNote)))
