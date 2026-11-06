"""The few calls Notion memory makes, on Notion's REST API (version 2026-03-11).

Not the Notion MCP connection: that reaches whatever the signed-in account can see, and it can
only rewrite a page whole. A Notion connection's token reaches only the pages shared with it, so
Notion itself keeps memory where it was put, and each note is a row of its own: added without
touching anyone else's, with its own edit time, and gone from the query when it's deleted.
"""

import time
from dataclasses import dataclass
from typing import Any, cast

import httpx2

from ringkeeper.errors import RingkeeperError

API = "https://api.notion.com/v1"
VERSION = "2026-03-11"
TIMEOUT = 20.0
PAGE = 100
MOST_ROWS = 2000  # beyond this a sync stops short, so it forgets nothing
LONGEST_WAIT_S = 5.0
TEXT_LIMIT = 2000  # Notion's limit on one piece of rich text
# Tests swap in httpx2.MockTransport; production talks to Notion.
transport: httpx2.BaseTransport | None = None

JSON = dict[str, Any]
NOTE, AGENT, DATE, TASK = "Note", "Agent", "Date", "Source task"
TEXT_BLOCKS = (
    "paragraph", "bulleted_list_item", "numbered_list_item", "to_do", "quote", "callout",
    "heading_1", "heading_2", "heading_3", "toggle",
)  # fmt: skip


class NotionError(RingkeeperError):
    """Notion refused, or didn't answer. The message is for the user."""


@dataclass(frozen=True)
class Place:
    """Where a project's notes are."""

    database: str
    data_source: str
    url: str
    columns: dict[str, str]  # what each column is called there: note, agent, date, task


@dataclass(frozen=True)
class Row:
    id: str
    edited: str
    text: str
    agent: str | None
    date: str | None
    task: str | None


def _why(status: int, found: JSON) -> str:
    said = str(found.get("message") or "")[:200]
    if status == 401:
        return "Notion refused this connection's token. Add the connection again."
    if status in (403, 404):
        return (
            "Notion can't reach that. In Notion, open the page, choose ••• > Connections, and "
            "add this connection."
        )
    return f"Notion answered {status}: {said}" if said else f"Notion answered {status}."


def call(
    token: str, method: str, path: str, body: JSON | None = None, params: JSON | None = None
) -> JSON:
    headers = {"Authorization": f"Bearer {token}", "Notion-Version": VERSION}
    with httpx2.Client(timeout=TIMEOUT, transport=transport, follow_redirects=False) as client:
        for last in (False, True):
            try:
                answer = client.request(
                    method, f"{API}{path}", headers=headers, json=body, params=params
                )
            except httpx2.HTTPError:
                raise NotionError("Notion didn't answer. Try again in a moment.") from None
            wait = float(answer.headers.get("retry-after") or 1)
            if answer.status_code in (429, 529) and not last and wait <= LONGEST_WAIT_S:
                time.sleep(wait)
                continue
            try:
                found = cast(JSON, answer.json())
            except ValueError:
                found = {}
            if answer.status_code >= 400:
                raise NotionError(_why(answer.status_code, found))
            return found
    raise NotionError("Notion is busy. Try again in a moment.")


def _plain(pieces: Any) -> str:
    return "".join(str(p.get("plain_text") or "") for p in cast(list[JSON], pieces or []))


def _title(found: JSON) -> str:
    if found.get("object") == "data_source":
        return _plain(found.get("title"))
    for value in cast(dict[str, JSON], found.get("properties") or {}).values():
        if value.get("type") == "title":
            return _plain(value.get("title"))
    return ""


def who(token: str) -> str:
    return str(call(token, "GET", "/users/me").get("name") or "Notion")


def find(token: str, words: str = "") -> list[JSON]:
    """What this connection was given: pages to put a database under, and databases to use."""
    body: JSON = {"page_size": PAGE,
                  "sort": {"timestamp": "last_edited_time", "direction": "descending"}}  # fmt: skip
    if words:
        body["query"] = words
    results = cast(list[JSON], call(token, "POST", "/search", body).get("results") or [])
    return [
        {"id": r.get("id"), "kind": r.get("object"), "title": _title(r) or "Untitled",
         "url": r.get("url")}
        for r in results
        if r.get("object") in ("page", "data_source") and r.get("url")  # not a partial answer
    ]  # fmt: skip


def _text(content: str) -> list[JSON]:
    return [{"type": "text", "text": {"content": content[:TEXT_LIMIT]}}]


def create_place(token: str, parent: str, title: str) -> Place:
    """A database of notes under `parent`."""
    made = call(token, "POST", "/databases", {
        "parent": {"type": "page_id", "page_id": parent},
        "title": _text(title),
        "initial_data_source": {"properties": {
            NOTE: {"title": {}}, AGENT: {"rich_text": {}}, DATE: {"date": {}},
            TASK: {"rich_text": {}},
        }},
    })  # fmt: skip
    sources = cast(list[JSON], made.get("data_sources") or [])
    if not sources:
        raise NotionError("Notion made the database but didn't say how to reach it.")
    columns = {"note": NOTE, "agent": AGENT, "date": DATE, "task": TASK}
    return Place(str(made["id"]), str(sources[0]["id"]), str(made.get("url") or ""), columns)


def use_place(token: str, data_source: str) -> Place:
    """A database that's already there. Columns it doesn't have are left out of each note."""
    found = call(token, "GET", f"/data_sources/{data_source}")
    props = cast(dict[str, JSON], found.get("properties") or {})
    title = next((name for name, p in props.items() if p.get("type") == "title"), None)
    if title is None:
        raise NotionError("That isn't a Notion database Ringkeeper can keep notes in.")
    columns = {"note": title}
    for key, name, kind in (("agent", AGENT, "rich_text"), ("date", DATE, "date"),
                            ("task", TASK, "rich_text")):  # fmt: skip
        if (props.get(name) or {}).get("type") == kind:
            columns[key] = name
    parent = cast(JSON, found.get("parent") or {})
    return Place(str(parent.get("database_id") or ""), data_source, str(found.get("url") or ""),
                 columns)  # fmt: skip


def _row(found: JSON, columns: dict[str, str]) -> Row:
    props = cast(dict[str, JSON], found.get("properties") or {})

    def rich(key: str) -> str | None:
        value = props.get(columns.get(key, "")) or {}
        return _plain(value.get("rich_text")) or None

    day = cast(JSON, (props.get(columns.get("date", "")) or {}).get("date") or {})
    return Row(
        str(found["id"]), str(found.get("last_edited_time") or ""),
        _plain((props.get(columns["note"]) or {}).get("title")),
        rich("agent"), cast(str | None, day.get("start")), rich("task"),
    )  # fmt: skip


def rows(token: str, place: Place) -> list[Row]:
    """Every note there. Raises rather than answer with part of them: what's missing from this
    list is forgotten."""
    found: list[Row] = []
    cursor: str | None = None
    while True:
        body: JSON = {"page_size": PAGE}
        if cursor:
            body["start_cursor"] = cursor
        page = call(token, "POST", f"/data_sources/{place.data_source}/query", body)
        results = cast(list[JSON], page.get("results") or [])
        found += [_row(r, place.columns) for r in results if not r.get("in_trash")]
        if page.get("request_status") or len(found) > MOST_ROWS:
            raise NotionError(f"That database has more than {MOST_ROWS} notes: tidy it up first.")
        if not page.get("has_more"):
            return found
        cursor = cast(str | None, page.get("next_cursor"))


def body_of(token: str, page: str) -> str:
    """What's written inside a note's page, for someone who wrote there and not in the column."""
    lines: list[str] = []
    cursor: str | None = None
    while True:
        params: JSON = {"page_size": PAGE}
        if cursor:
            params["start_cursor"] = cursor
        found = call(token, "GET", f"/blocks/{page}/children", params=params)
        for block in cast(list[JSON], found.get("results") or []):
            kind = str(block.get("type"))
            if kind in TEXT_BLOCKS:
                line = _plain(cast(JSON, block.get(kind) or {}).get("rich_text"))
                if line:
                    lines.append(line)
        if not found.get("has_more"):
            return "\n".join(lines)
        cursor = cast(str | None, found.get("next_cursor"))


def add(
    token: str, place: Place, text: str, agent: str | None, day: str, task: str | None
) -> tuple[str, str]:
    """A new row: its id and edit time."""
    columns = place.columns
    props: JSON = {columns["note"]: {"title": _text(text)}}
    if "agent" in columns and agent:
        props[columns["agent"]] = {"rich_text": _text(agent)}
    if "date" in columns:
        props[columns["date"]] = {"date": {"start": day}}
    if "task" in columns and task:
        props[columns["task"]] = {"rich_text": _text(task)}
    made = call(token, "POST", "/pages", {
        "parent": {"type": "data_source_id", "data_source_id": place.data_source},
        "properties": props,
    })  # fmt: skip
    return str(made["id"]), str(made.get("last_edited_time") or "")


def trash(token: str, page: str) -> None:
    call(token, "PATCH", f"/pages/{page}", {"in_trash": True})
