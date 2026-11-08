"""Cal.com, through its hosted MCP server (a fake of it here): reads run, bookings and every other
change wait each time, deleting needs the name typed, and a project can be kept to one team."""

import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from ringkeeper import destructive, remote
from ringkeeper.adapters import mcp
from ringkeeper.api import routes
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import (
    bindings,
    calls,
    decisions,
    env_scan,
    instances,
    mcp_oauth,
    mcp_tools,
    remote_connections,
    secrets_in,
)
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import get_vault
from tests.helpers import make_project

SERVER = Path(__file__).parent / "fixtures" / "fake_calcom_mcp.py"
NAME = "gi-calcom"
CLIENT = "general-informatics"
TEAM = "11"
CAL = "https://mcp.cal.com"


def add(ran: Path) -> None:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)], "env": {"FAKE_CALCOM_RAN": str(ran)},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(session, get_settings(), NAME, InstanceType.MCP, secret)
        instances.create_instance(session, InstanceIn(
            name=NAME, title="Cal.com", type=InstanceType.MCP, env=Env.PROD, secret_ref=ref,
            owner=CLIENT, config={"remote": "calcom"},
        ))  # fmt: skip


@pytest.fixture
def ran(database_url: str, tmp_path: Path) -> Path:
    """A client's Cal.com: `gi-web` reaches the whole account, `gi-support` only team 11."""
    file = tmp_path / "ran.json"
    with session_scope() as session:
        make_project(session, "gi-web", owner=CLIENT)
        make_project(session, "gi-support", owner=CLIENT)
    add(file)
    with session_scope() as session:
        bindings.set_binding(session, BindingIn(project="gi-web", instance=NAME))
        bindings.set_binding(
            session, BindingIn(project="gi-support", instance=NAME, scope={"team": TEAM})
        )
    mcp_tools.refresh(NAME)
    return file


def call(tool: str, project: str = "gi-web", **arguments: Any) -> Any:
    args = {"tool": tool, "arguments": arguments}
    who = Caller(project, "session-1", "token-1")
    made = ToolCall(who, InstanceType.MCP, "call_tool", NAME, args)
    return asyncio.run(run_call(made, get_vault()))


def happened(file: Path) -> list[str]:
    return [r["tool"] for r in json.loads(file.read_text())] if file.exists() else []


def approve(held: dict[str, Any]) -> Any:
    return asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault()))


def decision(held: dict[str, Any]) -> Any:
    with session_scope() as session:
        found = decisions.get_decision(session, held["decision_id"])
        return found, routes._decision_out(found)  # pyright: ignore[reportPrivateUsage]


# --- The shelf and signing in ---


def test_its_on_the_shelf_and_signs_in() -> None:
    found = remote.BY_KEY["calcom"]
    assert found.url == f"{CAL}/mcp"
    assert (found.signin, found.paste) == (True, None)  # the hosted server takes no API key
    assert found.guard == "scheduling"
    assert remote.GUARDS["scheduling"].never_trusted
    shelf = {s["key"]: s for s in remote_connections.catalog_shelf()}
    assert shelf["calcom"]["category"] == "Other"


def test_signing_in_registers_with_calcom(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cal.com's metadata as its server gives it: it is its own authorization server, and lets
    any app register."""
    registered: list[dict[str, Any]] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        url = str(request.url)
        if url == f"{CAL}/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(200, json={
                "resource": f"{CAL}/mcp", "authorization_servers": [CAL],
                "bearer_methods_supported": ["header"],
            })  # fmt: skip
        if url == f"{CAL}/.well-known/oauth-authorization-server":
            return httpx2.Response(200, json={
                "issuer": CAL, "authorization_endpoint": f"{CAL}/oauth/authorize",
                "token_endpoint": f"{CAL}/oauth/token",
                "registration_endpoint": f"{CAL}/oauth/register",
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "client_id_metadata_document_supported": True,
            })  # fmt: skip
        if url == f"{CAL}/oauth/register":
            registered.append(json.loads(request.content))
            return httpx2.Response(201, json={"client_id": "cal-client"})
        return httpx2.Response(404)

    monkeypatch.setattr(mcp_oauth, "transport", httpx2.MockTransport(handle))
    redirect = "http://127.0.0.1:5000/oauth/callback"
    page = mcp_oauth.start(f"{CAL}/mcp", redirect, {"name": NAME})
    query = {k: v[0] for k, v in parse_qs(urlsplit(page).query).items()}
    assert page.startswith(f"{CAL}/oauth/authorize?")
    assert (query["client_id"], query["resource"]) == ("cal-client", f"{CAL}/mcp")
    # It registers rather than using the desktop-only client document, so a server install
    # can sign in too.
    assert registered[0]["redirect_uris"] == [redirect]


def test_an_api_key_in_a_projects_env_is_seen_and_left_there(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("CAL_API_KEY=cal_live_abcdefghijklmnop\n")
    found = [row[0] for row in env_scan.scan(tmp_path) if row[0].label == "Cal.com"]
    assert len(found) == 1
    assert found[0].usable is False
    assert "signs in" in found[0].why


def test_another_clients_project_cant_use_it(ran: Path) -> None:
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        with pytest.raises(RuleViolationError, match="can only be used by that client"):
            bindings.set_binding(session, BindingIn(project="acme-web", instance=NAME))


# --- Reads run ---


@pytest.mark.parametrize(
    ("tool", "level"),
    [
        ("get_bookings", mcp.READ), ("get_event_types", mcp.READ), ("get_availability", mcp.READ),
        ("get_busy_times", mcp.READ), ("list_contacts", mcp.READ), ("search_docs", mcp.READ),
        ("find_api_operation", mcp.READ), ("describe_api_operation", mcp.READ),
        ("get_app_link", mcp.READ), ("calculate_routing_form_slots", mcp.READ),
        ("create_booking", mcp.APPROVE), ("reschedule_booking", mcp.APPROVE),
        ("cancel_booking", mcp.APPROVE), ("confirm_booking", mcp.APPROVE),
        ("mark_booking_absent", mcp.APPROVE), ("add_booking_attendee", mcp.APPROVE),
        ("create_event_type", mcp.APPROVE), ("update_event_type", mcp.APPROVE),
        ("duplicate_event_type", mcp.APPROVE), ("update_schedule", mcp.APPROVE),
        ("create_team_invite", mcp.APPROVE), ("update_me", mcp.APPROVE),
        ("edit_routing_form_routes", mcp.APPROVE),
        ("delete_event_type", mcp.CONFIRM), ("delete_schedule", mcp.CONFIRM),
        ("delete_team_membership", mcp.CONFIRM), ("remove_contact", mcp.CONFIRM),
        ("unassign_attribute_from_user", mcp.CONFIRM),
    ],
)  # fmt: skip
def test_what_each_tool_needs(tool: str, level: str) -> None:
    assert mcp.rule({"remote": "calcom"}, tool).level == level


def test_reads_run(ran: Path) -> None:
    assert "Intro call" in call("get_bookings")["text"]
    assert "intro-call" in call("get_event_types")["text"]
    assert happened(ran) == []


# --- Bookings wait, every time ---


def test_a_booking_waits_and_runs_once_approved(ran: Path) -> None:
    attendee = {"name": "Pat Lee", "email": "pat@example.com", "timeZone": "UTC"}
    held = call("create_booking", eventTypeId=501, start="2026-11-20T15:00:00Z", attendee=attendee)
    assert held["status"] == "held_for_approval"
    assert happened(ran) == []
    approve(held)
    assert happened(ran) == ["create_booking"]


def test_cancelling_waits_and_cant_be_trusted_for_an_hour(ran: Path) -> None:
    held = call("cancel_booking", bookingUid="bk-1")
    assert held["status"] == "held_for_approval"
    _, card = decision(held)
    assert card.trustable is False
    args = {"tool": "cancel_booking"}
    assert destructive.never_trusted(InstanceType.MCP, "call_tool", args, {"remote": "calcom"})


def test_a_booking_cant_be_set_to_run_without_asking() -> None:
    config = {"remote": "calcom", "tool_rules": {"create_booking": mcp.READ}}
    assert mcp.rule(config, "create_booking").level == mcp.APPROVE


# --- Deleting needs the name typed ---


def test_deleting_an_event_type_needs_the_name_typed(ran: Path) -> None:
    held = call("delete_event_type", eventTypeId=501)
    assert held["status"] == "held_for_approval"
    found, _ = decision(held)
    assert decisions.confirmation_needed(found)
    assert happened(ran) == []


def test_the_name_typed_cant_be_lowered() -> None:
    config = {"remote": "calcom", "tool_rules": {"delete_event_type": mcp.APPROVE}}
    assert mcp.rule(config, "delete_event_type").level == mcp.CONFIRM
    args: dict[str, Any] = {"tool": "delete_event_type", "arguments": {"eventTypeId": 1}}
    why = destructive.reason(InstanceType.MCP, "call_tool", args, {"remote": "calcom"})
    assert why is not None
    assert "deletes" in why


# --- Its one tool for any API operation ---


@pytest.mark.parametrize(
    ("operation", "level"),
    [
        ("getBookings", mcp.READ), ("listEventTypes", mcp.READ),
        ("createBooking", mcp.APPROVE), ("BookingsController_cancelBooking", mcp.APPROVE),
        ("deleteEventType", mcp.CONFIRM), ("TeamsController_deleteTeam", mcp.CONFIRM),
        ("", mcp.CONFIRM),
    ],
)  # fmt: skip
def test_any_api_operation_goes_by_what_it_does(operation: str, level: str) -> None:
    arguments: dict[str, Any] = {"operationId": operation, "params": {}}
    assert mcp.call_rule({"remote": "calcom"}, "call_api_operation", arguments).level == level


# --- One team ---


def test_a_team_link_fills_in_its_team(ran: Path) -> None:
    answer = call("get_event_types", project="gi-support")
    assert '"teamId":11' in answer["text"].replace(" ", "")


def test_a_team_link_refuses_another_team(ran: Path) -> None:
    with pytest.raises(RefusalError, match=r"limited to cal\.com team '11'"):
        call("get_event_types", project="gi-support", teamId=12)
    with pytest.raises(RefusalError, match="limited to"):
        call("delete_event_type", project="gi-support", eventTypeId=501, teamId=12)
    assert happened(ran) == []


def test_a_team_link_refuses_what_reaches_the_whole_account(ran: Path) -> None:
    for tool in ("get_bookings", "get_my_teams"):
        with pytest.raises(RefusalError, match=r"whole Cal\.com account"):
            call(tool, project="gi-support")
    with pytest.raises(RefusalError, match=r"whole Cal\.com account"):
        call("call_api_operation", project="gi-support", operationId="getBookings")


def test_a_team_link_still_reads_the_help_and_who_you_are(ran: Path) -> None:
    assert "Event types" in call("search_docs", project="gi-support", query="event types")["text"]
    assert "general-informatics" in call("get_me", project="gi-support")["text"]


def test_a_team_links_delete_runs_on_that_team_once_the_name_is_typed(ran: Path) -> None:
    held = call("delete_event_type", project="gi-support", eventTypeId=501)
    assert held["status"] == "held_for_approval"
    asyncio.run(calls.approve(held["decision_id"], by="owner", vault=get_vault(), confirm=NAME))
    assert json.loads(ran.read_text())[-1] == {
        "tool": "delete_event_type", "eventTypeId": 501, "teamId": 11,
    }  # fmt: skip
