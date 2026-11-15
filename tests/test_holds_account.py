"""After a connection is added, which projects start ticked: every one for a tool that holds no
account (a browser, files on this computer), none for one that signs in or holds a key."""

import json

from ringkeeper.api.manage_routes import holds_account, list_connections
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import instances, secrets_in

BROWSER = json.dumps({"command": "npx", "args": ["@playwright/mcp@0.0.41"]})
FILES = json.dumps(
    {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Work"]}
)
WITH_KEY = json.dumps(
    {"command": "npx", "args": ["acme-crm-mcp"], "env": {"ACME_API_KEY": "placeholder-key"}}
)
HEADER = json.dumps(
    {"url": "https://mcp.example.com/mcp", "headers": {"Authorization": "Bearer placeholder-token"}}
)
SIGNED_IN = json.dumps(
    {"url": "https://mcp.example.com/mcp", "oauth": {"access_token": "placeholder-token"}}
)
KEY_IN_ARGS = json.dumps({"command": "npx", "args": ["demo-mcp", "--token=placeholder"]})


def test_a_browser_or_files_holds_no_account() -> None:
    assert holds_account(InstanceType.MCP, BROWSER) is False
    assert holds_account(InstanceType.MCP, FILES) is False
    assert holds_account(InstanceType.MCP, None) is False  # nothing stored at all


def test_an_mcp_server_with_a_key_or_sign_in_holds_one() -> None:
    for secret in (WITH_KEY, HEADER, SIGNED_IN, KEY_IN_ARGS):
        assert holds_account(InstanceType.MCP, secret) is True, secret


def test_a_cloud_login_holds_one_with_no_key_stored() -> None:
    login = {"remote": "gcloud", "source": "login", "preview": "ops@harbor-bakery.example"}
    assert holds_account(InstanceType.MCP, BROWSER, login) is True
    assert holds_account(InstanceType.MCP, BROWSER, {"source": "open"}) is False


def test_every_other_kind_of_connection_holds_one() -> None:
    for kind in InstanceType:
        if kind is not InstanceType.MCP:
            assert holds_account(kind, "secret") is True, kind
            assert holds_account(kind, None) is True, kind  # its key just isn't readable now


def test_the_connection_list_says_which(database_url: str) -> None:
    rows = {"desk-browser": (InstanceType.MCP, BROWSER), "acme-crm": (InstanceType.MCP, WITH_KEY)}
    with session_scope() as session:
        for name, (kind, secret) in rows.items():
            ref = secrets_in.store_connection(session, get_settings(), name, kind, secret)
            instances.create_instance(session, InstanceIn(
                name=name, type=kind, env=Env.DEV, secret_ref=ref, owner=instances.SHARED_OWNER,
                config={}, shared=True,
            ))  # fmt: skip
    with session_scope() as session:
        listed = {c.name: c.holds_account for c in list_connections(session)}
    assert listed == {"acme-crm": True, "desk-browser": False}
