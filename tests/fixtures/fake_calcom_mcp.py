"""A tiny stand-in for Cal.com's hosted MCP server, with a few of its tools as Cal.com names
them (they wrap its API v2, so IDs are camelCase). What really ran goes to FAKE_CALCOM_RAN."""

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

server = FastMCP("fake-calcom")
RAN = Path(os.environ["FAKE_CALCOM_RAN"])


def did(tool: str, **arguments: Any) -> dict[str, Any]:
    done: list[Any] = json.loads(RAN.read_text()) if RAN.exists() else []
    RAN.write_text(json.dumps([*done, {"tool": tool, **arguments}]))
    return {"status": "success", "tool": tool, **arguments}


@server.tool
def search_docs(query: str) -> dict[str, Any]:
    """Search Cal.com's docs."""
    return {"results": [{"title": "Event types", "query": query}]}


@server.tool
def get_me() -> dict[str, Any]:
    """Your profile."""
    return {"data": {"id": 7, "username": "general-informatics", "timeZone": "America/New_York"}}


@server.tool
def get_my_teams() -> dict[str, Any]:
    """Teams you belong to."""
    return {"data": [{"id": 11, "name": "Support"}, {"id": 12, "name": "Sales"}]}


@server.tool
def get_event_types(teamId: int | None = None) -> dict[str, Any]:  # noqa: N803 (Cal.com's name)
    """List event types."""
    return {"data": [{"id": 501, "slug": "intro-call", "teamId": teamId}]}


@server.tool
def get_bookings(status: str | None = None) -> dict[str, Any]:
    """List your bookings."""
    return {"data": [{"uid": "bk-1", "title": "Intro call", "status": status or "upcoming"}]}


@server.tool
def get_org_team_bookings(orgId: int, teamId: int) -> dict[str, Any]:  # noqa: N803
    """A team's bookings."""
    return {"data": [{"uid": "bk-9", "orgId": orgId, "teamId": teamId}]}


@server.tool
def create_booking(eventTypeId: int, start: str, attendee: dict[str, Any]) -> dict[str, Any]:  # noqa: N803
    """Create a booking."""
    return did("create_booking", eventTypeId=eventTypeId, start=start)


@server.tool
def cancel_booking(bookingUid: str, cancellationReason: str = "") -> dict[str, Any]:  # noqa: N803
    """Cancel a booking."""
    return did("cancel_booking", bookingUid=bookingUid)


@server.tool
def delete_event_type(eventTypeId: int, teamId: int | None = None) -> dict[str, Any]:  # noqa: N803
    """Delete an event type."""
    return did("delete_event_type", eventTypeId=eventTypeId, teamId=teamId)


@server.tool
def call_api_operation(operationId: str, params: dict[str, Any] | None = None) -> dict[str, Any]:  # noqa: N803
    """Run any API v2 operation."""
    return did("call_api_operation", operationId=operationId)


if __name__ == "__main__":
    server.run()
