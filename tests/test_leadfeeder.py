"""Leadfeeder, against a fake of its MCP server: reads run, changes wait, credits show on the card
before anything spends them, deletes need the name typed, and one approval is enough."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper import leadfeeder, remote
from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import ToolError
from ringkeeper.api import routes
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    bindings,
    calls,
    decisions,
    env_scan,
    instances,
    mcp_tools,
    secrets_in,
)
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import get_vault
from tests import test_web_api
from tests.helpers import make_project

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
SERVER = Path(__file__).parent / "fixtures" / "fake_leadfeeder_mcp.py"
NAME = "acme-leadfeeder"
ACCOUNT = "acc-1"
JOB = {"account_id": ACCOUNT, "data": {"attributes": {"contact_ids": ["1", "2", "3"]}}}


def add(name: str, client: str, ran: Path) -> None:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)],
        "env": {"FAKE_LEADFEEDER_RAN": str(ran)},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, get_settings(), name, InstanceType.MCP, secret)
        instances.create_instance(session, InstanceIn(
            name=name, title=name, type=InstanceType.MCP, env=Env.PROD, secret_ref=ref,
            owner=client, config={"remote": "leadfeeder"},
        ))  # fmt: skip


@pytest.fixture
def ran(database_url: str, tmp_path: Path) -> Path:
    file = tmp_path / "ran.json"
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
    add(NAME, "acme", file)
    with session_scope() as session:
        bindings.set_binding(session, BindingIn(
            project="acme-web", instance=NAME, scope={"account": ACCOUNT},
        ))  # fmt: skip
    mcp_tools.refresh(NAME)
    return file


def caller() -> Caller:
    return Caller("acme-web", "session-1", "token-1")


def call(tool: str, **arguments: Any) -> Any:
    args = {"tool": tool, "arguments": arguments}
    made = ToolCall(caller(), InstanceType.MCP, "call_tool", NAME, args)
    return asyncio.run(run_call(made, get_vault()))


def happened(file: Path) -> list[str]:
    return [r["tool"] for r in json.loads(file.read_text())] if file.exists() else []


def approve(held: dict[str, Any]) -> Any:
    return asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault()))


def card(held: dict[str, Any]) -> Any:
    with session_scope() as session:
        return routes._decision_out(decisions.get_decision(session, held["decision_id"]))  # pyright: ignore[reportPrivateUsage]


# --- The shelf ---


def test_its_on_the_shelf_with_sign_in_only() -> None:
    found = remote.BY_KEY["leadfeeder"]
    assert found.url == "https://mcp.leadfeeder.com/mcp"
    assert found.signin is True
    assert found.paste is None  # Leadfeeder's server takes no API key
    assert found.shelf is True
    assert found.scope_required is True


def test_an_api_key_in_a_projects_env_is_seen_and_left_there(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("LEADFEEDER_API_KEY=abcdefghijklmnop\n")
    found = [row[0] for row in env_scan.scan(tmp_path) if row[0].label == "Leadfeeder"]
    assert len(found) == 1
    assert found[0].usable is False
    assert "signs in" in found[0].why
    assert found[0].keys == ["LEADFEEDER_API_KEY"]


# --- Reads ---


def test_reads_run_and_stay_in_the_projects_account(ran: Path) -> None:
    answer = call("search_web_visits", start_date="2026-11-01", end_date="2026-11-06")
    assert "Visitor Ltd" in answer["text"]
    assert ACCOUNT in answer["text"]  # filled in from the link


def test_another_account_is_refused(ran: Path) -> None:
    with pytest.raises(RefusalError, match="limited to"):
        call(
            "search_web_visits", account_id="acc-2", start_date="2026-11-01", end_date="2026-11-02"
        )


def test_listing_accounts_shows_only_the_linked_one(ran: Path) -> None:
    assert ACCOUNT in call("get_account_info")["text"]


def test_reference_data_needs_no_account(ran: Path) -> None:
    assert "DE" in call("get_countries")["text"]


@pytest.mark.parametrize("tool", sorted(leadfeeder.FREE_READS))
def test_free_reads_run_whatever_their_names_say(tool: str) -> None:
    assert mcp.rule({"remote": "leadfeeder"}, tool).level == mcp.READ


def test_a_link_with_no_account_is_refused(ran: Path) -> None:
    with session_scope() as session:
        make_project(session, "acme-blog", owner="acme")
        bindings.set_binding(session, BindingIn(project="acme-blog", instance=NAME))
    args = {"tool": "search_web_visits", "arguments": {"start_date": "a", "end_date": "b"}}
    made = ToolCall(Caller("acme-blog", "s", "t"), InstanceType.MCP, "call_tool", NAME, args)
    with pytest.raises(RefusalError, match="doesn't say which"):
        asyncio.run(run_call(made, get_vault()))


# --- Changes wait, and one approval is enough ---


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("create_tag", {"name": "Hot"}), ("add_company_to_lists", {"id": "9"})],
)
def test_a_change_waits_and_runs_once_approved(
    ran: Path, tool: str, arguments: dict[str, Any]
) -> None:
    held = call(tool, **arguments)
    assert held["status"] == "held_for_approval"
    assert happened(ran) == []
    approve(held)
    assert happened(ran) == [tool]  # Leadfeeder's own confirmation, answered by that approval


def test_the_token_never_reaches_the_agent(ran: Path) -> None:
    approve(call("create_tag", name="Hot"))
    with session_scope() as session:
        rows = session.scalars(select(AuditEvent)).all()
        assert all("token-for" not in f"{e.args} {e.result}" for e in rows)


def test_an_agent_cant_confirm_for_itself(ran: Path) -> None:
    with pytest.raises((RefusalError, ToolError), match="confirms it after your approval"):
        call("create_tag", name="Hot", confirm_token="token-for-create_tag")
    assert happened(ran) == []


def test_nothing_here_can_be_trusted_for_an_hour(ran: Path) -> None:
    assert card(call("create_tag", name="Hot")).trustable is False


# --- Credits ---


def test_a_contact_job_waits_with_its_estimate_on_the_card(ran: Path) -> None:
    held = call("create_find_contact_data_job", **JOB)
    assert held["status"] == "held_for_approval"
    shown = card(held)
    assert shown.money["amount"] == "About 3 credits"
    assert shown.money["counterparty"] == "Find contact details"
    assert shown.money["who"] == "For"
    assert shown.money["account"] == ACCOUNT
    assert happened(ran) == []
    approve(held)
    assert happened(ran) == ["create_find_contact_data_job"]


def test_an_enrichment_job_waits_with_its_estimate_on_the_card(ran: Path) -> None:
    held = call("create_company_enrichment_job", account_id=ACCOUNT, data={
        "attributes": {"custom_field_id": "score", "list_id": "company_1"},
    })  # fmt: skip
    assert card(held).money["amount"] == "About 250 credits"


def test_an_estimate_asks_for_the_job_as_written() -> None:
    found = leadfeeder.estimate("create_company_enrichment_job", {"account_id": "a", "data": {
        "attributes": {"custom_field_id": "f", "company_ids": ["1"], "locale": "de-DE"},
    }})  # fmt: skip
    assert found == ("estimate_company_enrichment_job", {"account_id": "a", "data": {
        "type": "enrichment_job", "attributes": {"custom_field_id": "f", "company_ids": ["1"]},
    }})  # fmt: skip


@pytest.mark.parametrize(
    "attributes",
    [{"enrichment_strategy": "preview", "prompt": "x"},
     {"custom_field_id": "f", "advanced_search": {"filters": {}}}],
)  # fmt: skip
def test_a_job_with_no_estimate_says_so_and_guesses_nothing(attributes: dict[str, Any]) -> None:
    arguments = {"account_id": "a", "data": {"attributes": attributes}}
    tool = "create_company_enrichment_job"
    assert leadfeeder.estimate(tool, arguments) is None
    assert leadfeeder.cost(tool, arguments, None) == "Credits not known"


def test_a_search_of_a_known_size_is_estimated_by_that_size() -> None:
    attributes = {"custom_field_id": "f", "advanced_search": {"filters": {}, "page": {"size": 40}}}
    found = leadfeeder.estimate(
        "create_company_enrichment_job", {"account_id": "a", "data": {"attributes": attributes}}
    )
    assert found is not None
    assert found[1]["data"]["attributes"]["company_count"] == 40


def test_opening_a_contact_waits_though_it_reads(ran: Path) -> None:
    held = call("get_contact", id="7")
    assert held["status"] == "held_for_approval"
    assert card(held).money["amount"] == "Up to 1 credit"
    approve(held)
    assert happened(ran) == ["get_contact"]


def test_opening_many_shows_the_most_it_can_cost(ran: Path) -> None:
    assert card(call("get_contacts", ids="1,2,3,4")).money["amount"] == "Up to 4 credits"


def test_a_change_that_spends_nothing_shows_no_credits(ran: Path) -> None:
    assert card(call("create_tag", name="Hot")).money is None


def test_when_the_estimate_fails_the_card_says_it_isnt_known() -> None:
    assert leadfeeder.cost("create_find_contact_data_job", {}, None) == "Credits not known"


@pytest.mark.parametrize("tool", sorted(leadfeeder.CREDIT_TOOLS))
def test_every_tool_that_spends_waits_even_before_a_tool_list(tool: str) -> None:
    assert mcp.rule({"remote": "leadfeeder"}, tool).level != mcp.READ


def test_a_new_tool_that_needs_confirming_waits_by_its_arguments(ran: Path) -> None:
    held = call("get_new_paid_thing")
    assert held["status"] == "held_for_approval"


def test_one_that_slips_through_stops_and_waits_next_time(ran: Path) -> None:
    """Listed without the argument, it reads by its name: Leadfeeder's answer says otherwise."""
    with session_scope() as session:
        found = instances.get_instance(session, NAME)
        known = {**found.config["arguments"], "get_new_paid_thing": ["account_id"]}
        found.config = {**found.config, "arguments": known}
    with pytest.raises(ToolError, match="needs your approval"):
        call("get_new_paid_thing")
    assert happened(ran) == []
    assert call("get_new_paid_thing")["status"] == "held_for_approval"


# --- Deletes ---


def test_a_delete_needs_the_name_typed(ran: Path) -> None:
    held = call("delete_list", id="company_1")
    assert held["status"] == "held_for_approval"
    with session_scope() as session:
        found = decisions.get_decision(session, held["decision_id"])
        assert decisions.confirmation_needed(found)


def test_privacy_settings_need_the_name_typed_too(ran: Path) -> None:
    held = call("update_cookie_consent_settings", enabled=False)
    with session_scope() as session:
        assert decisions.confirmation_needed(decisions.get_decision(session, held["decision_id"]))


# --- Accounts ---


def test_test_lists_the_accounts_to_choose_from(owner: TestClient, ran: Path) -> None:
    said = owner.post(f"/connections/{NAME}/test").json()["message"]
    assert "Demo (acc-1)" in said
