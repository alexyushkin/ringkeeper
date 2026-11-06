"""Notion for agent memory: a connection's token, which reaches only what's shared with it.

Separate from the Notion MCP connection in the catalog, which is for general work and keeps its
own holds. This one is never an agent's tool: services.memory uses it, pinned to one database per
project. It can only say whose it is, for the Test button.
"""

from typing import cast

from ringkeeper.adapters.vendor_spec import JSON, Secret, Vendor, reads
from ringkeeper.services.notion_api import VERSION

CATEGORY = "Other"


def _who(found: JSON) -> str:
    bot = cast(JSON, found.get("bot") or {})
    return str(bot.get("workspace_name") or found.get("name") or "Notion")


NOTION_MEMORY = Vendor(
    "notion_memory", "Notion memory", CATEGORY, "Agents' notes, in a Notion database",
    "https://api.notion.com/v1",
    secrets=(Secret("api_key", "Notion connection token",
                    pattern=r"^(ntn_|secret_)[A-Za-z0-9]+$"),),
    headers={"Notion-Version": VERSION},
    probe="whoami", who=_who,
    words=("notion", "memory", "notes", "knowledge"),
    key_url="https://www.notion.so/profile/integrations",
    steps="In Notion, create an internal connection and copy its token. Then open the page the "
    "notes go under, choose ••• > Connections, and add it.",
    note="For agent memory only: choose it in a project's Settings. It reaches only pages you "
    "share with it. Agents never get it as a tool.",
    ops=(reads("whoami", "/users/me", "Which Notion workspace this is"),),
)  # fmt: skip

VENDORS = (NOTION_MEMORY,)
