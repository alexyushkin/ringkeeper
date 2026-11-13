"""Several people, each with their own Ringkeeper and Notion connection, sharing one Notion
memory: what's already under the root is joined, not made again; two candidates, or a title
close to the one wanted, are asked about; a token that can't write keeps notes here until it can;
each note says who saved it. One client's page still never holds another's notes, and My memory
stays its owner's own."""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import MemoryNote
from ringkeeper.services import memory, projects, runtime
from tests.helpers import make_project
from tests.test_memory import (
    PARENT,
    FakeNotion,
    admin,
    fresh,
    notion,
    owner,
    said,
    setup_code,
    to_notion,
    worker,
)

__all__ = ["admin", "notion", "owner", "setup_code"]  # fixtures

ACME_PAGE, GLOBEX_PAGE = "acme › Ringkeeper", "globex › Ringkeeper"  # noqa: RUF001


def title(project: str) -> str:
    with session_scope() as session:
        return memory.title_of(session, projects.get_project(session, project))


def everywhere() -> dict[str, Any]:
    with session_scope() as session:
        return memory.use_everywhere(session, "team-notion", PARENT)


def under(fake: FakeNotion, parent: str) -> list[str]:
    return sorted(fake.titles[i] for i, p in fake.parents.items() if p == parent)


def place_of(project: str) -> dict[str, Any]:
    with session_scope() as session:
        return memory.settings_of(projects.get_project(session, project))


# --- Joining what's there ---


def test_a_second_install_joins_the_pages_and_databases_already_there(notion: FakeNotion) -> None:
    # Someone else's Ringkeeper, on the same root page, set up Acme's page and one database.
    page = notion.page_under(PARENT, ACME_PAGE)
    web = notion.database_under(page, title("acme-web"))
    done = everywhere()
    assert sorted(done["set_up"]) == ["acme-blog", "acme-web", "globex-app"]
    assert done["joined"] == [ACME_PAGE, title("acme-web")]
    assert sorted(done["created"]) == sorted([title("acme-blog"), GLOBEX_PAGE, title("globex-app")])
    assert done["ask"] == []
    assert under(notion, PARENT) == [ACME_PAGE, GLOBEX_PAGE]  # no second Acme page
    assert under(notion, page) == sorted([title("acme-blog"), title("acme-web")])
    assert place_of("acme-web")["notion"]["data_source"] == web
    assert place_of("acme-web")["notion"]["parent"] == page
    memory.remember(worker("acme-web"), "Invoices go out on the 1st")
    assert [r["text"] for r in notion.databases[web].values()] == ["Invoices go out on the 1st"]


def test_its_notes_are_read_by_the_other_install_too(notion: FakeNotion) -> None:
    page = notion.page_under(PARENT, ACME_PAGE)
    web = notion.database_under(page, title("acme-web"))
    notion.row(web, "Their note, from the other install")
    everywhere()
    fresh()
    assert said(memory.recall(worker("acme-web"))) == ["Their note, from the other install"]


def test_two_pages_with_the_title_are_asked_about_never_guessed(notion: FakeNotion) -> None:
    first = notion.page_under(PARENT, ACME_PAGE)
    second = notion.page_under(PARENT, ACME_PAGE)
    done = everywhere()
    assert done["set_up"] == ["globex-app"]  # Acme's projects wait for the answer
    [ask] = done["ask"]
    assert (ask["key"], ask["what"], ask["label"]) == ("client:acme", "page", "acme")
    assert sorted(o["id"] for o in ask["options"]) == sorted([first, second])
    with session_scope() as session:
        assert list(memory.every_settings(session)["asks"]) == ["client:acme"]
        chosen = memory.choose_found(session, "client:acme", second)
    assert sorted(chosen["set_up"]) == ["acme-blog", "acme-web"]
    assert chosen["joined"] == [ACME_PAGE]
    assert place_of("acme-web")["notion"]["parent"] == second
    with session_scope() as session:
        assert memory.every_settings(session)["asks"] == {}


def test_a_close_title_is_asked_about_and_a_new_one_can_be_made(notion: FakeNotion) -> None:
    close = notion.page_under(PARENT, "Acme › Ringkeeper")  # noqa: RUF001 (not "acme")
    [ask] = everywhere()["ask"]
    assert [o["id"] for o in ask["options"]] == [close]
    with session_scope() as session:
        chosen = memory.choose_found(session, "client:acme", memory.NEW)
    assert chosen["created"][0] == ACME_PAGE
    assert sorted(under(notion, PARENT)) == sorted(["Acme › Ringkeeper", ACME_PAGE, GLOBEX_PAGE])  # noqa: RUF001


def test_only_an_offered_one_can_be_chosen(notion: FakeNotion) -> None:
    notion.page_under(PARENT, ACME_PAGE)
    notion.page_under(PARENT, ACME_PAGE)
    everywhere()
    with session_scope() as session, pytest.raises(RuleViolationError, match="offered"):
        memory.choose_found(session, "client:acme", PARENT)
    with session_scope() as session, pytest.raises(RuleViolationError, match="nothing to choose"):
        memory.choose_found(session, "client:globex", memory.NEW)


def test_a_new_project_with_a_close_database_waits_and_says_so(notion: FakeNotion) -> None:
    everywhere()
    page = place_of("acme-web")["notion"]["parent"]
    with session_scope() as session:
        make_project(session, "acme-shop", owner="acme", title="Shop")
    notion.database_under(page, title("acme-shop").replace("Shop", "Shop "))
    memory.follow_new("acme-shop")  # a task starts anyway, with notes here
    assert place_of("acme-shop").get("notion") is None
    with session_scope() as session:
        assert list(memory.every_settings(session)["asks"]) == ["project:acme-shop"]


def test_another_projects_database_isn_t_taken_for_a_close_one(notion: FakeNotion) -> None:
    with session_scope() as session:
        make_project(session, "acme-webs", owner="acme", title="Acme webs")
    done = everywhere()
    assert done["ask"] == []  # "Acme web" and "Acme webs": each this install's own
    assert sorted(done["set_up"]) == ["acme-blog", "acme-web", "acme-webs", "globex-app"]


# --- Sharing stays safe ---


def test_a_joined_client_page_still_holds_only_that_client(notion: FakeNotion) -> None:
    page = notion.page_under(PARENT, ACME_PAGE)
    everywhere()
    with session_scope() as session, pytest.raises(RuleViolationError, match="another client"):
        memory.attach(session, "globex-app", "team-notion", parent=page)


def test_a_database_titled_for_another_client_is_refused(notion: FakeNotion) -> None:
    theirs = notion.database_under(PARENT, title("globex-app"))
    with session_scope() as session, pytest.raises(RuleViolationError, match="for globex"):
        memory.attach(session, "acme-web", "team-notion", data_source=theirs)


def test_someone_s_my_memory_is_never_joined_or_offered(notion: FakeNotion) -> None:
    theirs = notion.database_under(PARENT, "My memory")
    with session_scope() as session, pytest.raises(RuleViolationError, match="its owner's own"):
        memory.attach(session, "acme-web", "team-notion", data_source=theirs)
    with session_scope() as session, pytest.raises(RuleViolationError, match="made new for you"):
        memory.attach_mine(session, "team-notion", data_source=theirs)
    with session_scope() as session, pytest.raises(RuleViolationError, match="its owner's own"):
        memory.attach_shared(session, "team-notion", data_source=theirs)
    from ringkeeper.services import notion_api  # noqa: PLC0415

    found = [
        {"object": "data_source", "id": theirs, "url": "u", "title": [{"plain_text": "My memory"}]},
        {"object": "data_source", "id": "x", "url": "u", "title": [{"plain_text": "Notes"}]},
    ]
    notion_api.transport = None  # find() reads what /search returns
    assert [r["title"] for r in _found(found)] == ["Notes"]


def _found(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    import httpx2  # noqa: PLC0415

    from ringkeeper.services import notion_api  # noqa: PLC0415

    def answer(_request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json={"results": results, "has_more": False})

    notion_api.transport = httpx2.MockTransport(answer)
    return notion_api.find("ntn_fake")


# --- A token that can't write ---


def test_a_read_only_token_keeps_notes_here_until_it_can_write(
    owner: TestClient, notion: FakeNotion
) -> None:
    source = to_notion("acme-web", "acme-notion")
    notion.read_only.add(source)
    kept = memory.remember(worker("acme-web"), "Kept here for now")
    assert kept["where"] == (
        "this project's notes (Acme web), until Notion lets this connection write there"
    )
    by_hand = memory.keep("acme-web", "Also kept here")
    assert by_hand["where"].endswith("until Notion lets this connection write there")
    assert notion.databases[source] == {}
    shown = owner.get("/projects/acme-web/memory").json()
    assert "Insufficient" not in str(shown["cant_write"])  # Notion's own words aren't shown
    assert "Connections" in shown["cant_write"]
    assert shown["waiting"] == 2
    listed = owner.get("/projects/acme-web/memory/notes").json()
    assert [n["waiting"] for n in listed] == [True, True]
    fresh()
    recalled = said(memory.recall(worker("acme-web")))
    assert sorted(recalled) == ["Also kept here", "Kept here for now"]
    # Notion lets it write again: what waited goes there, oldest first, and the warning goes.
    notion.read_only.clear()
    memory.remember(worker("acme-web"), "Written straight away")
    texts = [r["text"] for r in notion.databases[source].values()]
    assert texts[0] == "Written straight away"
    assert sorted(texts[1:]) == ["Also kept here", "Kept here for now"]
    shown = owner.get("/projects/acme-web/memory").json()
    assert (shown["cant_write"], shown["waiting"]) == (None, 0)
    assert not any(n["waiting"] for n in owner.get("/projects/acme-web/memory/notes").json())
    with session_scope() as session:
        assert all(n.external_id for n in session.scalars(select(MemoryNote)))


def test_notion_down_still_says_so_and_keeps_nothing(notion: FakeNotion) -> None:
    to_notion("acme-web", "acme-notion")
    notion.down = True
    from ringkeeper.services import notion_api  # noqa: PLC0415

    with pytest.raises(notion_api.NotionError):
        memory.remember(worker("acme-web"), "Lost")


# --- Who saved it ---


def test_saved_by_is_the_owner_s_name_on_notes_from_tasks_and_by_hand(notion: FakeNotion) -> None:
    source = to_notion("acme-web", "acme-notion")
    with session_scope() as session:
        runtime.update(session, {"owner_name": "Ana Torres"}, by="owner")
    memory.remember(worker("acme-web"), "From a task")
    memory.keep("acme-web", "By hand")
    typed = notion.row(source, "Typed in Notion by someone")
    assert sorted(notion.saved_by.values()) == ["Ana Torres", "Ana Torres"]
    fresh()
    memory.recall(worker("acme-web"))
    assert typed not in notion.saved_by  # a row people typed stays as it is
    assert not [c for c in notion.calls if c[0] == "PATCH" and c[1].startswith("/pages/")]


def test_without_a_name_saved_by_is_left_empty(notion: FakeNotion) -> None:
    to_notion("acme-web", "acme-notion")
    memory.remember(worker("acme-web"), "No name set")
    assert notion.saved_by == {}


# --- Through the API ---


def test_choosing_through_the_api(owner: TestClient, notion: FakeNotion) -> None:
    first = notion.page_under(PARENT, ACME_PAGE)
    notion.page_under(PARENT, ACME_PAGE)
    body = owner.put("/memory/every", json={"connection": "team-notion", "root": PARENT}).json()
    assert [a["key"] for a in body["ask"]] == ["client:acme"]
    assert [a["key"] for a in owner.get("/memory/every").json()["asks"]] == ["client:acme"]
    chosen = owner.put("/memory/every/choose", json={"key": "client:acme", "choice": first})
    assert chosen.status_code == 200, chosen.text
    assert chosen.json()["asks"] == []
    assert sorted(chosen.json()["set_up"]) == ["acme-blog", "acme-web"]
    assert owner.get("/settings").json()["values"]["owner_name"] == ""
