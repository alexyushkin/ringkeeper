"""The services added from the same official servers Ringkeeper Connect uses: each is on the
shelf as Beta, its kind's rules hold what reaches real people, and Calendly's sign-in comes back
to an address it accepts."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from ringkeeper import remote
from ringkeeper.adapters import mcp
from ringkeeper.api import remote_routes
from ringkeeper.config import Settings, get_settings
from ringkeeper.services import mcp_oauth, needs, remote_connections
from tests import test_web_api

# The signed-in owner's client, as in the web API's tests.
setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

ADDED = (
    "make", "zapier", "klaviyo", "brevo", "kit", "mailerlite", "customerio", "customerio_eu",
    "instantly", "lemlist", "hunter", "intercom", "intercom_eu", "trello", "basecamp",
    "todoist", "canva", "typeform", "jotform", "tally", "fireflies", "granola", "otter",
    "calendly", "vapi", "tiktok_ads", "ahrefs", "semrush", "buffer", "hootsuite", "metricool",
)  # fmt: skip


def level(key: str, tool: str) -> str:
    return mcp.rule({"remote": key}, tool).level


def test_each_is_on_the_shelf_as_beta_with_a_note() -> None:
    shelf = {s["key"]: s for s in remote_connections.catalog_shelf()}
    for key in ADDED:
        service = remote.BY_KEY[key]
        assert service.beta, key
        assert shelf[key]["beta"], key
        assert service.url.startswith("https://"), key
        assert service.signin or service.paste, key
        assert service.note, key
        assert service.guard in remote.GUARDS, key
    # What was already there stays Official.
    assert not shelf["stripe"]["beta"]


def test_every_category_a_service_names_is_one_the_catalog_shows() -> None:
    source = (Path(__file__).parents[1] / "web" / "components" / "catalog.tsx").read_text(
        encoding="utf-8"
    )
    listed = re.search(r"const CATEGORIES = \[(.*?)\] as const", source)
    assert listed is not None
    categories = set(re.findall(r'"([^"]+)"', listed.group(1)))
    for service in remote.CATALOG:
        assert not service.category or service.category in categories, service.key


def test_instantly_takes_a_key_because_it_lets_only_partners_sign_in() -> None:
    found = remote.BY_KEY["instantly"]
    assert (found.signin, found.paste) == (False, "Instantly API key")
    assert found.auth_scheme == "Bearer"


def test_semrush_sends_its_key_the_way_semrush_reads_it() -> None:
    assert remote.BY_KEY["semrush"].auth_scheme == "Apikey"


@pytest.mark.parametrize(
    ("service", "tool", "needs_level"),
    [
        # Email marketing and outreach: reading campaigns runs, drafts wait, sending needs the name.
        ("klaviyo", "get_campaigns", mcp.READ),
        ("klaviyo", "list_email_campaigns", mcp.READ),
        ("klaviyo", "create_campaign", mcp.APPROVE),
        ("klaviyo", "send_campaign", mcp.CONFIRM),
        ("mailerlite", "schedule_campaign", mcp.CONFIRM),
        ("kit", "create_broadcast", mcp.APPROVE),
        ("brevo", "send_sms_campaign", mcp.CONFIRM),
        ("customerio", "delete_segment", mcp.CONFIRM),
        ("lemlist", "start_campaign", mcp.CONFIRM),
        ("instantly", "activate_campaign", mcp.CONFIRM),
        ("instantly", "list_leads", mcp.READ),
        ("hunter", "add_lead", mcp.APPROVE),
        ("hunter", "delete_lead", mcp.CONFIRM),
        # Social: a post made or changed can go live.
        ("buffer", "get_posts", mcp.READ),
        ("buffer", "create_post", mcp.CONFIRM),
        ("hootsuite", "schedule_message", mcp.CONFIRM),
        ("metricool", "update_post", mcp.CONFIRM),
        ("metricool", "get_analytics", mcp.READ),
        # Ads, scheduling and voice use the kinds already there.
        ("tiktok_ads", "update_campaign_budget", mcp.CONFIRM),
        ("tiktok_ads", "create_campaign", mcp.APPROVE),
        ("calendly", "cancel_event", mcp.APPROVE),
        ("vapi", "create_call", mcp.APPROVE),
        # Everything else: changes wait.
        ("trello", "update_card", mcp.APPROVE),
        ("basecamp", "create_message", mcp.APPROVE),
        ("zapier", "gmail_send_email", mcp.APPROVE),
        ("make", "run_scenario", mcp.APPROVE),
        ("intercom", "search_conversations", mcp.READ),
    ],
)
def test_what_each_new_service_needs(service: str, tool: str, needs_level: str) -> None:
    assert level(service, tool) == needs_level


@pytest.mark.parametrize("key", ["calendly", "vapi"])
def test_bookings_and_calls_are_never_trusted(key: str) -> None:
    assert remote.GUARDS[remote.BY_KEY[key].guard].never_trusted


def desktop_request(host: str = "127.0.0.1:5000") -> Request:
    return Request({
        "type": "http", "scheme": "http", "server": ("127.0.0.1", 5000), "path": "/",
        "root_path": "", "query_string": b"", "headers": [(b"host", host.encode())],
    })  # fmt: skip


def test_calendlys_sign_in_comes_back_to_localhost_in_the_desktop_app() -> None:
    """Calendly refuses an http address that isn't localhost ("must start with https in
    Production"), and the desktop app answers on both."""
    settings = get_settings().model_copy(update={"mode": "desktop"})
    usual = remote_routes.redirect_uri(desktop_request(), settings)
    assert usual == "http://127.0.0.1:5000/oauth/callback"
    calendly = remote_routes.redirect_uri(desktop_request(), settings, localhost=True)
    assert calendly == "http://localhost:5000/oauth/callback"
    assert [r.key for r in remote.CATALOG if r.localhost] == ["calendly"]


@pytest.mark.parametrize(("key", "localhost"), [("calendly", True), ("klaviyo", False)])
def test_starting_a_sign_in_asks_for_localhost_only_for_calendly(
    owner: TestClient, monkeypatch: pytest.MonkeyPatch, key: str, localhost: bool
) -> None:
    asked: list[bool] = []

    def where(_request: Request, _settings: Settings, *, localhost: bool = False) -> str:
        asked.append(localhost)
        return "http://localhost:5000/oauth/callback"

    monkeypatch.setattr(remote_routes, "redirect_uri", where)

    def start(*_args: object, **_kw: object) -> str:
        return "https://example.com/authorize"

    monkeypatch.setattr(mcp_oauth, "start", start)
    body = {"remote": key, "name": f"acme-{key}", "owner": "acme", "env": "dev"}
    started = owner.post("/connections/signin", json=body)
    assert started.status_code == 200, started.text
    assert asked == [localhost]


def test_a_server_install_keeps_its_own_address_for_calendly_too() -> None:
    settings = get_settings().model_copy(
        update={"mode": "server", "web_origin": "https://rk.example.com"}
    )
    found = remote_routes.redirect_uri(desktop_request(), settings, localhost=True)
    assert found == "https://rk.example.com/oauth/callback"


def test_calendly_and_tiktok_ads_count_for_calendars_and_ads() -> None:
    calendar, ads = needs.need("calendar"), needs.need("ads")
    assert calendar is not None
    assert "calendly" in calendar.remotes
    assert ads is not None
    assert "tiktok_ads" in ads.remotes
    assert needs.service_key("Customer.io") == "customerio"
    assert needs.service_key("ConvertKit") == "kit"
    assert needs.service_key("Zapier") == "zapier"
