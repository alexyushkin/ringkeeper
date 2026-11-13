"""Meta Ads, through Meta's own ads MCP server (a fake of it here): reads run, anything that
spends money or goes live needs the name typed, other changes wait, a project can be held to
one ad account, and signing in goes through your own Meta app."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from ringkeeper import remote
from ringkeeper.adapters import mcp
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    bindings,
    calls,
    decisions,
    developer_apps,
    instances,
    mcp_tools,
    projects,
    remote_connections,
    secrets_in,
    setup_check,
)
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.services.remote_connections import NewConnection
from ringkeeper.vault import get_vault
from tests.helpers import make_project

SERVER = Path(__file__).parent / "fixtures" / "fake_meta_ads_mcp.py"
NAME = "seaside-meta-ads"
CLIENT = "seaside-bakery"


# --- The shelf ---


def test_its_on_the_shelf_ahead_of_pipeboard() -> None:
    keys = [r.key for r in remote.CATALOG]
    assert keys.index("meta_ads") == keys.index("pipeboard") - 1
    found = remote.BY_KEY["meta_ads"]
    assert found.url == "https://mcp.facebook.com/ads"
    assert found.provider == "meta"
    assert set(found.provider_scopes) == {
        "ads_mcp_management", "ads_read", "ads_management", "catalog_management",
        "business_management", "pages_show_list", "instagram_basic",
    }  # fmt: skip
    assert "Create & manage ads with ads MCP server" in found.provider_steps
    assert "Your own ad accounts work straight away" in found.provider_steps
    assert "Advanced Access on ads_mcp_management" in found.provider_steps
    assert "expires" in found.paste_warning
    assert "about an hour" in found.paste_warning
    assert "30 calls a week" in remote.BY_KEY["pipeboard"].note
    shelf = {s["key"]: s for s in remote_connections.catalog_shelf()}
    assert shelf["meta_ads"]["provider"] == "meta"
    assert shelf["pipeboard"]["provider"] == ""


# --- What each tool needs ---


@pytest.mark.parametrize(
    ("tool", "level"),
    [
        # Reads run.
        ("ads_get_ad_accounts", mcp.READ), ("ads_get_ad_entities", mcp.READ),
        ("ads_get_ad_preview", mcp.READ), ("ads_get_creatives", mcp.READ),
        ("ads_library_search", mcp.READ), ("ads_get_opportunity_score", mcp.READ),
        ("ads_insights_performance_trend", mcp.READ), ("ads_insights_anomaly_signal", mcp.READ),
        ("ads_insights_auction_ranking_benchmarks", mcp.READ),
        ("ads_insights_industry_benchmark", mcp.READ),
        ("ads_insights_advertiser_context", mcp.READ), ("ads_get_errors", mcp.READ),
        ("ads_get_help_article", mcp.READ), ("ads_catalog_list_products", mcp.READ),
        ("ads_get_datasets", mcp.READ), ("ads_pixel_event_read", mcp.READ),
        ("ads_experiment_check_eligibility", mcp.READ),
        ("ads_account_get_activity_logs", mcp.READ),
        # Spends money or goes live: the name typed.
        ("ads_create_campaign", mcp.CONFIRM), ("ads_create_ad_set", mcp.CONFIRM),
        ("ads_create_ad", mcp.CONFIRM), ("ads_activate_entity", mcp.CONFIRM),
        ("ads_update_entity", mcp.CONFIRM), ("ads_boost_ig_post", mcp.CONFIRM),
        ("ads_delete_custom_audience", mcp.CONFIRM),
        ("ads_catalog_delete_product", mcp.CONFIRM),
        # Other changes wait for approval.
        ("ads_create_creative", mcp.APPROVE), ("ads_create_custom_audience", mcp.APPROVE),
        ("ads_update_custom_audience", mcp.APPROVE), ("ads_catalog_product_create", mcp.APPROVE),
        ("ads_catalog_update_product", mcp.APPROVE), ("ads_pixel_event_create", mcp.APPROVE),
        ("ads_experiment_abtest_create_test", mcp.APPROVE),
    ],
)  # fmt: skip
def test_what_each_tool_needs(tool: str, level: str) -> None:
    assert mcp.rule({"remote": "meta_ads"}, tool).level == level


def test_the_name_typed_cant_be_lowered() -> None:
    for tool in ("ads_create_campaign", "ads_activate_entity", "ads_update_entity"):
        config = {"remote": "meta_ads", "tool_rules": {tool: mcp.READ}}
        assert mcp.rule(config, tool).level == mcp.CONFIRM


def test_pipeboards_rules_are_unchanged() -> None:
    assert mcp.rule({"remote": "pipeboard"}, "create_campaign").level == mcp.APPROVE
    assert mcp.rule({"remote": "pipeboard"}, "update_campaign_budget").level == mcp.CONFIRM


# --- Through the fake server ---


def add(ran: Path) -> None:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)], "env": {"FAKE_META_ADS_RAN": str(ran)},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, get_settings(), NAME, InstanceType.MCP, secret)
        instances.create_instance(session, InstanceIn(
            name=NAME, title="Meta Ads", type=InstanceType.MCP, env=Env.PROD, secret_ref=ref,
            owner=CLIENT, config={"remote": "meta_ads"},
        ))  # fmt: skip


@pytest.fixture
def ran(database_url: str, tmp_path: Path) -> Path:
    """A client's Meta Ads: `seaside-web` reaches every ad account, `seaside-shop` only act_111."""
    file = tmp_path / "ran.json"
    with session_scope() as session:
        make_project(session, "seaside-web", owner=CLIENT)
        make_project(session, "seaside-shop", owner=CLIENT)
    add(file)
    with session_scope() as session:
        bindings.set_binding(session, BindingIn(project="seaside-web", instance=NAME))
        bindings.set_binding(
            session,
            BindingIn(project="seaside-shop", instance=NAME, scope={"account": "act_111"}),
        )
    mcp_tools.refresh(NAME)
    return file


def call(tool: str, project: str = "seaside-web", **arguments: Any) -> Any:
    args = {"tool": tool, "arguments": arguments}
    who = Caller(project, "session-1", "token-1")
    made = ToolCall(who, InstanceType.MCP, "call_tool", NAME, args)
    return asyncio.run(run_call(made, get_vault()))


def happened(file: Path) -> list[dict[str, Any]]:
    return json.loads(file.read_text()) if file.exists() else []


def test_reads_run(ran: Path) -> None:
    assert "Seaside Bakery" in call("ads_get_ad_accounts")["text"]
    assert "Spring sale" in call("ads_get_ad_entities", ad_account_id="act_111")["text"]
    assert "up" in call("ads_insights_performance_trend", ad_account_id="act_111")["text"]
    assert happened(ran) == []


def test_a_campaign_waits_for_the_name_typed(ran: Path) -> None:
    held = call("ads_create_campaign", ad_account_id="act_111", name="Spring", objective="SALES")
    assert held["status"] == "held_for_approval"
    with session_scope() as session:
        assert decisions.confirmation_needed(decisions.get_decision(session, held["decision_id"]))
    with pytest.raises(RuleViolationError):
        asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault()))
    assert happened(ran) == []
    asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault(), confirm=NAME))
    assert [r["tool"] for r in happened(ran)] == ["ads_create_campaign"]


def test_activating_waits_for_the_name_typed(ran: Path) -> None:
    held = call("ads_activate_entity", entity_id="c-1")
    assert held["status"] == "held_for_approval"
    with session_scope() as session:
        assert decisions.confirmation_needed(decisions.get_decision(session, held["decision_id"]))
    assert happened(ran) == []


def test_a_creative_waits_for_approval_only(ran: Path) -> None:
    held = call("ads_create_creative", ad_account_id="act_111", name="Loaf photo")
    assert held["status"] == "held_for_approval"
    with session_scope() as session:
        assert not decisions.confirmation_needed(
            decisions.get_decision(session, held["decision_id"])
        )
    asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault()))
    assert [r["tool"] for r in happened(ran)] == ["ads_create_creative"]


# --- One ad account ---


def test_an_account_link_fills_in_its_account(ran: Path) -> None:
    answer = call("ads_get_ad_entities", project="seaside-shop")
    assert "act_111" in answer["text"]


def test_an_account_link_takes_the_number_with_or_without_act(ran: Path) -> None:
    assert "Spring sale" in call("ads_get_ad_entities", project="seaside-shop",
                                 ad_account_id="111")["text"]  # fmt: skip
    assert "Spring sale" in call("ads_get_ad_entities", project="seaside-shop",
                                 ad_account_id="ACT_111")["text"]  # fmt: skip


def test_an_account_link_refuses_another_account(ran: Path) -> None:
    with pytest.raises(RefusalError, match="limited to ad account 'act_111'"):
        call("ads_get_ad_entities", project="seaside-shop", ad_account_id="act_222")
    with pytest.raises(RefusalError, match="limited to"):
        call("ads_create_campaign", project="seaside-shop", ad_account_id="222", name="X",
             objective="SALES")  # fmt: skip
    assert happened(ran) == []


def test_an_account_link_refuses_what_names_no_account(ran: Path) -> None:
    """Listing every ad account, or activating something by its own ID, could reach another
    client's account, so a link held to one account refuses them."""
    for tool, args in (("ads_get_ad_accounts", {}), ("ads_activate_entity", {"entity_id": "c-1"})):
        with pytest.raises(RefusalError, match="whole Meta Ads account"):
            call(tool, project="seaside-shop", **args)
    assert happened(ran) == []


def test_an_account_link_still_reads_the_help(ran: Path) -> None:
    answer = call("ads_get_help_article", project="seaside-shop", query="budgets")
    assert "Ad set budgets" in answer["text"]


def test_check_my_setup_counts_its_tools_without_a_model(ran: Path) -> None:
    with session_scope() as session:
        found, _ = setup_check.plan(session)
    mine = [p for p in found if p.connection == NAME]
    assert mine
    assert all(p.direct and p.request == setup_check.DIRECT_REQUEST for p in mine)


# --- Signing in through your own Meta app, or a pasted token ---


class Meta:
    """Facebook's token address answers GET, as Meta documents."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        query = request.url.params
        if request.url.path.endswith("/oauth/access_token"):
            if query.get("grant_type") == "fb_exchange_token":
                return httpx2.Response(200, json={"access_token": "long", "expires_in": 5184000})
            return httpx2.Response(200, json={"access_token": "short", "expires_in": 3600})
        return httpx2.Response(200, json={"id": "777", "name": "Pat Doe"})


@pytest.fixture
def meta(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Meta:
    fake = Meta()
    monkeypatch.setattr(developer_apps, "transport", httpx2.MockTransport(fake.handle))
    return fake


def settings() -> Settings:
    return get_settings().model_copy(update={"web_origin": "http://localhost:17845"})


def wanted(**fields: Any) -> dict[str, Any]:
    with session_scope() as session:
        make_project(session, "seaside-web", owner=CLIENT)
    new = NewConnection(
        name="seaside-meta", owner=CLIENT, env=Env.PROD, remote="meta_ads",
        project="seaside-web", scope={"account": "act_111"},
    )  # fmt: skip
    return {**new.as_dict(), **fields}


def test_signing_in_asks_meta_for_the_ads_permissions(meta: Meta) -> None:
    developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1")
    url, state = developer_apps.start_remote(settings(), "meta_ads", wanted())
    parts = urlsplit(url)
    query = parse_qs(parts.query)
    assert parts.netloc == "www.facebook.com"
    assert query["client_id"] == ["app-1"]
    assert query["redirect_uri"] == [developer_apps.redirect_for(
        settings(), developer_apps.provider("meta"))]  # fmt: skip
    assert query["scope"][0].split(",") == list(remote.META_ADS_SCOPES)
    assert query["state"] == [state]


def test_signing_in_adds_an_mcp_connection_with_a_lasting_token(meta: Meta) -> None:
    developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1")
    _, state = developer_apps.start_remote(settings(), "meta_ads", wanted())
    found = developer_apps.take(state)
    with session_scope() as session:
        name = developer_apps.finish(session, settings(), found, state, "code-1", {})
    assert name == "seaside-meta"
    assert developer_apps.outcome(state).state == "done"  # type: ignore[union-attr]
    with session_scope() as session:
        instance = instances.get_instance(session, name)
        assert instance.type is InstanceType.MCP
        assert instance.config["remote"] == "meta_ads"
        assert instance.config["account"] == "Pat Doe"
        assert len(instance.config["expires_at"]) == len("2027-01-12")
        secret = json.loads(get_vault().resolve(instance.secret_ref).reveal())
        project = projects.get_project(session, "seaside-web")
        link = next(b for b in project.bindings if b.instance.name == name)
        assert link.scope == {"account": "act_111"}
    assert secret["url"] == "https://mcp.facebook.com/ads"
    assert secret["headers"] == {"Authorization": "Bearer long"}  # the long-lived one
    exchanged = [r for r in meta.requests if r.url.params.get("grant_type") == "fb_exchange_token"]
    assert exchanged[0].url.params["fb_exchange_token"] == "short"


def test_signing_in_needs_your_meta_app_first(meta: Meta) -> None:
    with pytest.raises(developer_apps.SignInError, match="Developer apps"):
        developer_apps.start_remote(settings(), "meta_ads", wanted())


def test_only_a_service_with_an_app_signs_in_that_way() -> None:
    with pytest.raises(RuleViolationError, match="developer app"):
        developer_apps.start_remote(settings(), "pipeboard", {})


def test_a_pasted_token_goes_as_bearer(database_url: str) -> None:
    new = NewConnection(name="x", owner=CLIENT, env=Env.PROD, remote="meta_ads")
    secret = json.loads(remote_connections.token_secret(new, " EAAB token \n"))
    assert secret == {
        "url": "https://mcp.facebook.com/ads", "headers": {"Authorization": "Bearer EAABtoken"},
    }  # fmt: skip
