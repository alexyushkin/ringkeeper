"""A tiny stand-in for Meta's ads MCP server (mcp.facebook.com/ads), with a few of its tools as
Meta names them. Meta doesn't document their argument names; this fake uses ad_account_id. What
really ran goes to FAKE_META_ADS_RAN."""

import json
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP

server = FastMCP("fake-meta-ads")
RAN = Path(os.environ["FAKE_META_ADS_RAN"])


def did(tool: str, **arguments: Any) -> dict[str, Any]:
    done: list[Any] = json.loads(RAN.read_text()) if RAN.exists() else []
    RAN.write_text(json.dumps([*done, {"tool": tool, **arguments}]))
    return {"status": "success", "tool": tool, **arguments}


@server.tool
def ads_get_ad_accounts() -> dict[str, Any]:
    """List the ad accounts you can reach."""
    return {
        "data": [{"id": "act_111", "name": "Seaside Bakery"}, {"id": "act_222", "name": "Other"}]
    }


@server.tool
def ads_get_ad_entities(ad_account_id: str, level: str = "campaign") -> dict[str, Any]:
    """Campaigns, ad sets or ads in an ad account, with their numbers."""
    return {
        "data": [{"id": "c-1", "name": "Spring sale", "account": ad_account_id, "level": level}]
    }


@server.tool
def ads_insights_performance_trend(ad_account_id: str) -> dict[str, Any]:
    """How an ad account's results moved over time."""
    return {"trend": "up", "account": ad_account_id}


@server.tool
def ads_get_help_article(query: str) -> dict[str, Any]:
    """Search the Meta Business Help Center."""
    return {"results": [{"title": "Ad set budgets", "query": query}]}


@server.tool
def ads_create_campaign(ad_account_id: str, name: str, objective: str) -> dict[str, Any]:
    """Create a campaign, paused."""
    return did("ads_create_campaign", ad_account_id=ad_account_id, name=name, objective=objective)


@server.tool
def ads_create_creative(ad_account_id: str, name: str) -> dict[str, Any]:
    """Create a single-image link ad creative."""
    return did("ads_create_creative", ad_account_id=ad_account_id, name=name)


@server.tool
def ads_activate_entity(entity_id: str) -> dict[str, Any]:
    """Set a paused campaign, ad set or ad to active: it starts spending."""
    return did("ads_activate_entity", entity_id=entity_id)


if __name__ == "__main__":
    server.run()
