"""A tiny stand-in for Leadfeeder's MCP server, as its tools describe themselves: a change or a call
that may spend credits doesn't run, and answers with a one-time confirm_token. What really ran goes
to the file in FAKE_LEADFEEDER_RAN."""

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

server = FastMCP("fake-leadfeeder")
RAN = Path(os.environ["FAKE_LEADFEEDER_RAN"])
HOLD = {"readOnlyHint": False, "destructiveHint": False}


def ran() -> list[Any]:
    return json.loads(RAN.read_text()) if RAN.exists() else []


def confirmed(tool: str, token: str | None, **arguments: Any) -> dict[str, Any]:
    if token != f"token-for-{tool}":
        return {"status": "confirmation_required", "message": f"Run {tool}?",
                "data": {"confirm_token": f"token-for-{tool}"}}  # fmt: skip
    RAN.write_text(json.dumps([*ran(), {"tool": tool, **arguments}]))
    return {"status": "done", "tool": tool}


@server.tool(annotations={"readOnlyHint": True})
def get_account_info(account_id: str | None = None) -> dict[str, Any]:
    """List accounts."""
    return {"data": [{"id": account_id or "acc-1", "attributes": {"name": "Demo"}}]}


@server.tool(annotations={"readOnlyHint": True})
def search_web_visits(account_id: str, start_date: str, end_date: str) -> dict[str, Any]:
    """Search visits."""
    return {"data": [{"account": account_id, "company": "Visitor Ltd"}]}


@server.tool(annotations={"readOnlyHint": True})
def get_countries() -> dict[str, Any]:
    """Countries."""
    return {"data": ["DE"]}


@server.tool
def estimate_find_contact_data_job(account_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Estimate. No credits."""
    count = len(data["attributes"].get("contact_ids") or [1, 2, 3])
    return {"data": {"attributes": {"eligible_contacts": count, "estimated_total_credits": count}}}


@server.tool
def estimate_company_enrichment_job(account_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """Estimate. No credits."""
    return {"data": {"attributes": {"estimated_total_credits": 250}}}


@server.tool(annotations=HOLD)
def create_find_contact_data_job(
    account_id: str, data: dict[str, Any], confirm_token: str | None = None
) -> dict[str, Any]:
    """Find contact data. May consume credits."""
    return confirmed("create_find_contact_data_job", confirm_token, account_id=account_id)


@server.tool(annotations=HOLD)
def create_company_enrichment_job(
    account_id: str, data: dict[str, Any], confirm_token: str | None = None
) -> dict[str, Any]:
    """Enrich companies. May consume credits."""
    return confirmed("create_company_enrichment_job", confirm_token, account_id=account_id)


@server.tool(annotations={"readOnlyHint": True})
def get_contact(account_id: str, id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """A contact. 1 credit."""
    return confirmed("get_contact", confirm_token, id=id)


@server.tool(annotations={"readOnlyHint": True})
def get_contacts(account_id: str, ids: str, confirm_token: str | None = None) -> dict[str, Any]:
    """Contacts. 1 credit each."""
    return confirmed("get_contacts", confirm_token, ids=ids)


@server.tool(annotations=HOLD)
def create_tag(account_id: str, name: str, confirm_token: str | None = None) -> dict[str, Any]:
    """Make a tag."""
    return confirmed("create_tag", confirm_token, name=name)


@server.tool(annotations=HOLD)
def add_company_to_lists(
    account_id: str,
    id: str,
    confirm_token: str | None = None,
) -> dict[str, Any]:
    """Add a company to lists."""
    return confirmed("add_company_to_lists", confirm_token, id=id)


@server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def delete_list(account_id: str, id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """Delete a list."""
    return confirmed("delete_list", confirm_token, id=id)


@server.tool(annotations=HOLD)
def update_cookie_consent_settings(
    account_id: str, enabled: bool, confirm_token: str | None = None
) -> dict[str, Any]:
    """Cookie consent."""
    return confirmed("update_cookie_consent_settings", confirm_token, enabled=enabled)


@server.tool(annotations={"readOnlyHint": True})
def get_new_paid_thing(account_id: str, confirm_token: str | None = None) -> dict[str, Any]:
    """A tool Ringkeeper has never heard of, that turns out to need confirming."""
    return confirmed("get_new_paid_thing", confirm_token, account_id=account_id)


if __name__ == "__main__":
    server.run(show_banner=False)
