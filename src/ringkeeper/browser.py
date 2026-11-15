"""The browser (Playwright's MCP server, services.browser): what runs and what waits.

Going to a page, reading it, taking a screenshot and following a link run: that's what it's
for. Typing into a form, choosing in one, clicking a button that sends or buys or signs in,
and anything that downloads wait for approval in a client's project (services.calls), and
run in your own. Code in the page and anything that changes the browser's saved logins wait
everywhere.
"""

import re
from collections.abc import Mapping
from typing import Any

SOURCE = "browser"  # a connection's config["source"]: this browser

# Going and reading: they run.
READS = frozenset({
    "browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_take_screenshot",
    "browser_wait_for", "browser_tabs", "browser_close", "browser_resize", "browser_hover",
    "browser_find", "browser_console_messages", "browser_network_requests",
    "browser_network_request", "browser_get_config", "browser_highlight",
    "browser_hide_highlight", "browser_annotate", "browser_emulate_media",
})  # fmt: skip
# Code it runs (in the page, or on this computer), and what it reads or changes of the logins
# it keeps: wait everywhere, your own projects too.
EVERYWHERE = frozenset({
    "browser_evaluate", "browser_run_code_unsafe", "browser_cookie_get", "browser_cookie_list",
    "browser_cookie_set", "browser_cookie_delete", "browser_cookie_clear",
    "browser_storage_state", "browser_set_storage_state", "browser_localstorage_get",
    "browser_localstorage_list", "browser_localstorage_set", "browser_localstorage_delete",
    "browser_localstorage_clear", "browser_sessionstorage_get", "browser_sessionstorage_list",
    "browser_sessionstorage_set", "browser_sessionstorage_delete",
    "browser_sessionstorage_clear", "browser_route", "browser_unroute",
    "browser_network_state_set",
})  # fmt: skip
# A click on one of these sends, buys, signs in or downloads: it waits in a client's project.
SUBMITS = re.compile(
    r"\b(submit|send|sign[ -]?in|log[ -]?in|sign[ -]?up|register|buy|pay|purchase|order|"
    r"check[ -]?out|confirm|delete|remove|save|post|publish|apply|book|subscribe|place|"
    r"approve|accept|transfer|upload|download|export)\b",
    re.IGNORECASE,
)


def is_browser(config: Mapping[str, Any]) -> bool:
    return config.get("source") == SOURCE


def reads(tool: str, arguments: Mapping[str, Any] | None) -> bool:
    """Whether this call only goes somewhere or reads: it runs, in any project."""
    given = arguments or {}
    if tool in READS:
        return True
    if tool == "browser_click":
        named = " ".join(str(given.get(k) or "") for k in ("element", "ref", "button"))
        return not SUBMITS.search(named)
    if tool == "browser_press_key":  # Enter sends the form it's in
        return str(given.get("key") or "").strip().lower() not in {"enter", "numpadenter"}
    return False


def asks_everywhere(tool: str) -> bool:
    """Whether it waits in your own projects too, not only in a client's."""
    return tool in EVERYWHERE
