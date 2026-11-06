"""A tiny stand-in for Notion's hosted MCP server, with the memory tools as its docs describe:
notion-memory-search, notion-fetch with id "memory", and notion-create-pages with parent "memory".
What it saves goes to the file in FAKE_NOTION_SAVED, so a test can see what got through."""

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

server = FastMCP("fake-notion")
SAVED = Path(os.environ["FAKE_NOTION_SAVED"])


def saved() -> list[Any]:
    return json.loads(SAVED.read_text()) if SAVED.exists() else []


@server.tool(name="notion-memory-search", annotations={"readOnlyHint": True})
def memory_search(query: str, limit: int = 10) -> dict[str, Any]:
    """Search the user's memories."""
    return {"results": [{"id": "m1", "title": "Prefers short emails", "snippet": query}][:limit]}


@server.tool(name="notion-fetch", annotations={"readOnlyHint": True})
def fetch(id: str) -> dict[str, Any]:
    """Fetch a page, or "memory"."""
    return {"id": id, "text": "About me" if id == "memory" else "a page"}


@server.tool(
    name="notion-create-pages", annotations={"readOnlyHint": False, "destructiveHint": False}
)
def create_pages(pages: list[dict[str, Any]], parent: Any = None) -> dict[str, Any]:
    """Create pages."""
    SAVED.write_text(json.dumps([*saved(), {"parent": parent, "pages": pages}]))
    return {"pages": [{"id": "p1"}]}


@server.tool(
    name="notion-delete-pages", annotations={"readOnlyHint": False, "destructiveHint": True}
)
def delete_pages(page_ids: list[str], user_confirmed: bool = False) -> dict[str, Any]:
    """Move pages to the trash."""
    return {"trashed": page_ids if user_confirmed else []}


@server.tool(
    name="notion-update-page", annotations={"readOnlyHint": False, "destructiveHint": False}
)
def update_page(page_id: str, command: str = "") -> dict[str, Any]:
    """Update a page."""
    return {"page_id": page_id}


if __name__ == "__main__":
    server.run(show_banner=False)
