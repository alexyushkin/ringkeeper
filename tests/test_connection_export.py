"""A connection card's Copy tool list, Save tool list and Copy details for support: every kind
of connection, and never a token, a key or a client's data."""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ringkeeper import __version__
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import connection_export, instances, secrets_in
from tests import test_web_api
from tests.helpers import make_instance

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
SERVER = Path(__file__).parent / "fixtures" / "fake_meta_ads_mcp.py"
CLIENT = "seaside-bakery"
SECRET = "EAAB-made-up-token-123"


def add_mcp(tmp_path: Path) -> str:
    secret = json.dumps({
        "command": sys.executable, "args": [str(SERVER)],
        "env": {"FAKE_META_ADS_RAN": str(tmp_path / "ran.json"), "UNUSED_TOKEN": SECRET},
    })  # fmt: skip
    with session_scope() as session:
        ref = secrets_in.store_connection(
            session, get_settings(), "seaside-ads", InstanceType.MCP, secret
        )
        instances.create_instance(session, InstanceIn(
            name="seaside-ads", title="Meta Ads · Seaside Bakery", type=InstanceType.MCP,
            env=Env.PROD, secret_ref=ref, owner=CLIENT,
            config={"remote": "meta_ads", "source": "signin", "app": "a1",
                    "expires_at": "2027-01-12", "account": "Pat Doe"},
        ))  # fmt: skip
    return "seaside-ads"


def test_an_mcp_servers_tools_with_flags_rules_and_arguments(
    database_url: str, tmp_path: Path
) -> None:
    name = add_mcp(tmp_path)
    found = connection_export.tool_list(name)
    assert found["connection"] == name
    assert found["count"] == len(found["tools"]) == 7
    by = {t["name"]: t for t in found["tools"]}
    assert set(by["ads_get_ad_entities"]) == {
        "name", "read_only", "destructive", "rule", "arguments", "required", "about",
    }  # fmt: skip
    assert by["ads_get_ad_entities"]["arguments"] == ["ad_account_id", "level"]
    assert by["ads_get_ad_entities"]["required"] == ["ad_account_id"]
    assert by["ads_get_ad_entities"]["rule"] == connection_export.RUNS
    assert by["ads_get_ad_entities"]["about"] == (
        "Campaigns, ad sets or ads in an ad account, with their numbers."
    )
    assert by["ads_create_campaign"]["rule"] == connection_export.TYPED
    assert by["ads_create_creative"]["rule"] == connection_export.WAITS
    assert by["ads_activate_entity"]["required"] == ["entity_id"]
    assert SECRET not in json.dumps(found)


def test_a_built_in_connections_own_tools(database_url: str) -> None:
    with session_scope() as session:
        make_instance(session, "seaside-db", env=Env.PROD, owner=CLIENT)
    found = connection_export.tool_list("seaside-db")
    by = {t["name"]: t for t in found["tools"]}
    assert by
    reads = [t for t in found["tools"] if t["rule"] == connection_export.RUNS]
    assert reads
    for tool in found["tools"]:
        assert tool["read_only"] is None  # no server to say so
        assert set(tool["required"]) <= set(tool["arguments"])
        assert "target" not in tool["arguments"]


def test_a_business_systems_operations(database_url: str) -> None:
    with session_scope() as session:
        make_instance(session, "seaside-crm", type=InstanceType.VENDOR, owner=CLIENT,
                      config={"vendor": "pipedrive"})  # fmt: skip
    found = connection_export.tool_list("seaside-crm")
    assert found["kind"] == "Pipedrive"
    assert found["count"] > 5
    with_ids = [t for t in found["tools"] if t["required"]]
    assert with_ids
    assert all(set(t["required"]) <= set(t["arguments"]) for t in with_ids)
    assert {t["rule"] for t in found["tools"]} >= {connection_export.RUNS}


def test_details_for_support_say_how_it_connects_and_nothing_of_the_client(
    database_url: str, tmp_path: Path
) -> None:
    name = add_mcp(tmp_path)
    with session_scope() as session:
        instance = instances.get_instance(session, name)
        instance.last_test_at = datetime(2026, 11, 14, 9, 30, tzinfo=UTC)
        instance.last_test_ok = True
        instance.last_test_message = "Connected: 7 tools. Accounts: Seaside Bakery (act_111)."
        instance.config = {**instance.config, "annotations": {"a": {}, "b": {}, "c": {}}}
    with session_scope() as session:
        found = connection_export.support_details(session, name)
    lines = found["lines"]
    assert lines["Connection kind"] == "MCP server: Meta Ads"
    assert "your own app" in lines["Signs in with"]
    assert lines["Sign-in ends"] == "2027-01-12"
    assert lines["Last test"] == "worked, 2026-11-14 09:30 UTC"
    assert lines["Tools"] == "3"
    assert __version__ in lines["Ringkeeper"]
    text = found["text"]
    for private in ("Seaside", "seaside", "act_111", "Pat Doe", SECRET):
        assert private not in text


def test_a_pasted_key_and_a_connection_never_tested(database_url: str) -> None:
    with session_scope() as session:
        make_instance(session, "seaside-crm", type=InstanceType.VENDOR, owner=CLIENT,
                      config={"vendor": "pipedrive"})  # fmt: skip
        found = connection_export.support_details(session, "seaside-crm")
    assert found["lines"]["Signs in with"] == "A pasted key"
    assert found["lines"]["Sign-in ends"] == "Not known"
    assert found["lines"]["Last test"] == "Never tested"


@pytest.mark.usefixtures("database_url")
def test_the_routes_answer_the_owner(owner: TestClient) -> None:
    with session_scope() as session:
        make_instance(session, "seaside-db", owner=CLIENT)
    listed = owner.get("/connections/seaside-db/tool-list")
    assert listed.status_code == 200
    assert listed.json()["count"] == len(listed.json()["tools"])
    details = owner.get("/connections/seaside-db/support")
    assert details.status_code == 200
    assert "Connection kind: supabase" in details.json()["text"]
    assert owner.get("/connections/nothing-here/support").status_code == 404
