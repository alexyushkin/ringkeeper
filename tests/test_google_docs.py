"""Google Docs on the Google connection, against a fake of the Docs API: reading runs, making and
editing a document wait for approval every time, and an edit approved after the document changed
doesn't run."""

import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import google
from ringkeeper.adapters.base import Hold, Target, ToolError, ToolKind
from ringkeeper.models import Env
from ringkeeper.services import google_connections, google_oauth
from ringkeeper.vault import Secret

pytestmark = pytest.mark.anyio
DOC = "1AbCdEfGhIjKlMnOp"


def para(text: str) -> dict[str, Any]:
    return {"paragraph": {"elements": [{"textRun": {"content": text}}]}}


class FakeDocs:
    def __init__(self) -> None:
        self.revision = "rev-1"
        self.updates: list[dict[str, Any]] = []
        self.created: list[dict[str, Any]] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer at:info@geninfos.example"
        path = request.url.path
        if request.method == "GET" and path == f"/v1/documents/{DOC}":
            return httpx2.Response(200, json={
                "documentId": DOC, "title": "Q4 plan", "revisionId": self.revision,
                "body": {"content": [
                    {"sectionBreak": {}},
                    {"paragraph": {"elements": [{"textRun": {"content": "Goals\n"}}]}},
                    {"paragraph": {"elements": [
                        {"textRun": {"content": "Ship the "}},
                        {"textRun": {"content": "new site.\n"}},
                    ]}},
                    {"table": {"tableRows": [
                        {"tableCells": [
                            {"content": [para("Page\n")]}, {"content": [para("Owner\n")]},
                        ]},
                    ]}},
                ]},
            })  # fmt: skip
        if request.method == "POST" and path == "/v1/documents":
            self.created.append(json.loads(request.content))
            return httpx2.Response(200, json={"documentId": "new-doc-1", "title": "Brief"})
        if request.method == "POST" and path.endswith(":batchUpdate"):
            sent = json.loads(request.content)
            self.updates.append({"doc": path.split("/")[-1].split(":")[0], **sent})
            replies = [
                {"replaceAllText": {"occurrencesChanged": 2}} if "replaceAllText" in r else {}
                for r in sent["requests"]
            ]
            return httpx2.Response(200, json={"replies": replies})
        return httpx2.Response(404, json={"error": {"message": "not found"}})


@pytest.fixture
def docs() -> Iterator[FakeDocs]:
    fake = FakeDocs()
    before = google.transport
    google.transport = httpx2.MockTransport(fake.handle)
    yield fake
    google.transport = before


def account(*access: str) -> Target:
    secret = json.dumps({"access_token": "at:info@geninfos.example"})
    return Target("gi-google", Env.PROD, None, Secret("r", secret), config={"access": list(access)})


def tool(name: str) -> Any:
    return next(t for t in google.ADAPTER.tools if t.name == name)


def test_docs_access_asks_google_for_the_docs_scopes() -> None:
    assert google_oauth.ACCESS["docs_read"].scope.endswith("/auth/documents.readonly")
    assert google_oauth.ACCESS["docs_write"].scope.endswith("/auth/documents")
    assert "https://www.googleapis.com/auth/documents" in google_oauth.scopes_for(["docs_write"])


def test_reading_runs_and_every_change_waits_even_on_test_data() -> None:
    assert tool("read_doc").kind is ToolKind.READ
    for name in ("create_doc", "edit_doc"):
        assert tool(name).kind is ToolKind.WRITE
        assert tool(name).hold is Hold.ALWAYS


async def test_a_doc_reads_as_text_with_its_tables(docs: FakeDocs) -> None:
    found = await google.read_doc.handler(account("docs_read"), document_id=DOC)
    assert (found["title"], found["revision_id"]) == ("Q4 plan", "rev-1")
    assert found["text"] == "Goals\nShip the new site.\nPage\tOwner\n"


async def test_an_account_without_docs_access_is_told_how_to_add_it(docs: FakeDocs) -> None:
    with pytest.raises(ToolError, match=r"wasn't given \"Docs: read\" or \"Docs: read, create"):
        await google.read_doc.handler(account("sheets_read"), document_id=DOC)
    classify = tool("edit_doc").classify
    with pytest.raises(ToolError, match="Docs: read, create and edit"):
        classify(account("docs_read"), {"document_id": DOC, "append": "x"})


async def test_a_new_doc_gets_its_title_and_text(docs: FakeDocs) -> None:
    made = await google.create_doc.handler(account("docs_write"), title="Brief", text="Hello")
    assert made == {
        "created": "new-doc-1", "title": "Brief",
        "link": "https://docs.google.com/document/d/new-doc-1/edit",
    }  # fmt: skip
    assert docs.created == [{"title": "Brief"}]
    [update] = docs.updates
    assert update["requests"] == [{"insertText": {"text": "Hello", "endOfSegmentLocation": {}}}]


async def test_an_edit_replaces_and_appends_and_nothing_else(docs: FakeDocs) -> None:
    done = await google.edit_doc.handler(
        account("docs_write"), document_id=DOC,
        replace=[{"find": "new site", "replace_with": "new website"},
                 {"find": "q4", "replace_with": "Q4", "match_case": False}],
        append="Next: launch.",
    )  # fmt: skip
    assert done["replaced"] == [2, 2]
    [update] = docs.updates
    assert update["doc"] == DOC
    assert update["requests"] == [
        {"replaceAllText": {"containsText": {"text": "new site", "matchCase": True},
                            "replaceText": "new website"}},
        {"replaceAllText": {"containsText": {"text": "q4", "matchCase": False},
                            "replaceText": "Q4"}},
        {"insertText": {"text": "Next: launch.", "endOfSegmentLocation": {}}},
    ]  # fmt: skip


async def test_an_edit_that_says_nothing_is_refused(docs: FakeDocs) -> None:
    with pytest.raises(ToolError, match="say what to change"):
        await google.edit_doc.handler(account("docs_write"), document_id=DOC)
    assert docs.updates == []


async def test_an_edit_remembers_the_revision_it_was_asked_about(docs: FakeDocs) -> None:
    """Held, then the doc changes: the gateway compares these and refuses the stale edit."""
    precondition = tool("edit_doc").precondition
    assert precondition is not None
    args = {"document_id": DOC, "append": "x"}
    held = await precondition(account("docs_write"), **args)
    docs.revision = "rev-2"  # someone typed in it meanwhile
    assert await precondition(account("docs_write"), **args) != held


def test_an_edit_cant_be_set_to_run_without_asking() -> None:
    """A document may be someone else's: unlike other Google writes, the owner can't let an edit
    run by itself."""
    assert "edit_doc" in google.KEEPS_ASKING
    assert google_connections._rule({"edit_doc": "run"}, "edit_doc") == "ask"  # pyright: ignore[reportPrivateUsage]
    assert google_connections._rule({"create_doc": "run"}, "create_doc") == "run"  # pyright: ignore[reportPrivateUsage]
