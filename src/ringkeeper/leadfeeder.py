"""What Ringkeeper knows of Leadfeeder's MCP server beyond its tool list: which tools spend credits
and how to say what a call will cost before it runs.

Leadfeeder holds its own tools back: a call that changes something or may spend credits doesn't
run, and answers with a one-time `confirm_token`. Ringkeeper's approval is that confirmation, so
after it the call is repeated with the token (adapters.mcp), and an agent can't pass one itself.

A record bought once is free for 12 months, so a fixed cost is an upper limit, not a price.
"""

from collections.abc import Mapping
from typing import Any, cast

JSON = dict[str, Any]
CONFIRM_ARG = "confirm_token"
TOTAL_KEY = "estimated_total_credits"
ENRICH, FIND = "create_company_enrichment_job", "create_find_contact_data_job"
ONE_RECORD = frozenset({"get_company", "get_contact", "get_company_financials"})
MANY_RECORDS = frozenset({"get_companies_by_ids", "get_contacts", "get_company_ips"})
CREDIT_TOOLS = ONE_RECORD | MANY_RECORDS | {ENRICH, FIND}
# Reads whose names don't say so. Each says it uses no credits.
FREE_READS = frozenset({
    "estimate_company_enrichment_job", "estimate_find_contact_data_job", "match_companies", "usage",
})  # fmt: skip
# Tools with no account to narrow: reference data, the same for everyone.
OPEN_TOOLS = frozenset({"get_countries", "get_industries", "get_geo_suggestions"})
# What decides whether visitors are tracked and data is kept: the name typed, like a delete.
PRIVACY_TOOLS = frozenset({"update_cookie_consent_settings", "update_data_processing_settings"})
SAYS = {
    ENRICH: "Enrich companies", FIND: "Find contact details", "get_company": "Open a company",
    "get_contact": "Open a contact", "get_company_financials": "Read a company's financials",
    "get_companies_by_ids": "Open companies", "get_contacts": "Open contacts",
    "get_company_ips": "Read companies' IP data",
}  # fmt: skip


def _attributes(arguments: Mapping[str, Any]) -> JSON:
    data = cast(JSON, arguments.get("data") or {})
    return cast(JSON, data.get("attributes") or {})


def estimate(tool: str, arguments: Mapping[str, Any]) -> tuple[str, JSON] | None:
    """The free call that says what this job will cost, or None if there isn't one."""
    given = _attributes(arguments)
    kept: JSON
    if tool == FIND:
        name = "find_contact_data_job"
        kept = {k: given[k] for k in ("contact_ids", "list_id", "enrichment_type") if k in given}
    elif tool == ENRICH and given.get("enrichment_strategy") != "preview":
        name = "enrichment_job"
        keys = ("custom_field_id", "company_ids", "list_id", "enrichment_strategy", "limit",
                "prompt", "contact_collection")  # fmt: skip
        kept = {k: given[k] for k in keys if k in given}
        search = cast(JSON, given.get("advanced_search") or {})
        size = cast(JSON, search.get("page") or {}).get("size")
        if search and "list_id" not in kept and "company_ids" not in kept:
            if size is None:
                return None  # a search's size isn't known until it runs
            kept["company_count"] = size
    else:
        return None
    body = {"type": name, "attributes": kept}
    return f"estimate_{tool.removeprefix('create_')}", {
        "account_id": arguments.get("account_id"), "data": body,
    }  # fmt: skip


def total(found: Any) -> float | None:
    """The estimate's total, wherever the answer keeps it."""
    if isinstance(found, dict):
        answer = cast(JSON, found)
        if isinstance(answer.get(TOTAL_KEY), int | float):
            return float(answer[TOTAL_KEY])
        for inner in answer.values():
            if (deeper := total(inner)) is not None:
                return deeper
    elif isinstance(found, list):
        for inner in cast(list[Any], found):
            if (deeper := total(inner)) is not None:
                return deeper
    return None


def _records(arguments: Mapping[str, Any]) -> int | None:
    ids = arguments.get("ids")
    if isinstance(ids, str):
        return len([i for i in ids.split(",") if i.strip()])
    return len(cast(list[Any], ids)) if isinstance(ids, list) else None


def _credits(count: int) -> str:
    return f"Up to {count:,} credit{'' if count == 1 else 's'}"


def cost(tool: str, arguments: Mapping[str, Any], estimated: float | None) -> str:
    """What the card says, in large text. Nothing is ever said to be free or guessed at."""
    if estimated is not None:
        return f"About {estimated:,.0f} credit{'' if estimated == 1 else 's'}"
    if tool in ONE_RECORD:
        return _credits(1)
    if tool in MANY_RECORDS and (count := _records(arguments)) is not None:
        return _credits(count)
    return "Credits not known"


def what(tool: str) -> str:
    return SAYS.get(tool, tool.replace("_", " "))
