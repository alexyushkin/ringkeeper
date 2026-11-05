"""What's found on this computer and in a project: logins, MCP servers and more keys.
Each is offered, masked, and connected only when you confirm that one."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import cli_logins, env_scan, instances, project_servers
from ringkeeper.services.secrets_in import check_connection_secret
from tests import test_web_api

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


def found_in(folder: Path, text: str) -> dict[str, tuple[env_scan.Finding, str, str | None]]:
    (folder / ".env").write_text(text, encoding="utf-8")
    return {m[0].label: m for m in env_scan.scan(folder)}


# --- More keys in .env ---


def test_smtp_keys_become_an_email_connection(tmp_path: Path) -> None:
    found = found_in(
        tmp_path,
        "SMTP_HOST=smtp.acme.example\nSMTP_PORT=465\nSMTP_USER=hi@acme.example\n"
        "SMTP_PASSWORD=fake-pass-123\n",
    )
    finding, secret, _ = found["Email sending"]
    assert finding.service == "smtp"
    assert finding.usable
    assert "fake-pass-123" not in finding.masked
    fields = json.loads(secret)
    assert fields["security"] == "ssl"
    assert fields["from_address"] == "hi@acme.example"
    check_connection_secret(InstanceType.SMTP, secret)


def test_imap_keys_become_a_mailbox(tmp_path: Path) -> None:
    found = found_in(
        tmp_path, "IMAP_HOST=imap.acme.example\nIMAP_USER=a@acme.example\nIMAP_PASSWORD=pw-123456\n"
    )
    finding, secret, _ = found["Mailbox"]
    assert finding.service == "mailbox"
    assert json.loads(secret)["smtp_host"] == "smtp.acme.example"
    check_connection_secret(InstanceType.MAILBOX, secret)


def test_resend_needs_a_from_address(tmp_path: Path) -> None:
    alone = found_in(tmp_path, "RESEND_API_KEY=re_fake_123456\n")["Resend"][0]
    assert not alone.usable
    assert "From address" in alone.why
    finding, secret, _ = found_in(
        tmp_path, "RESEND_API_KEY=re_fake_123456\nRESEND_FROM=hi@acme.example\n"
    )["Resend"]
    assert finding.usable
    check_connection_secret(InstanceType.RESEND, secret)


def test_the_presets_keep_their_addresses(tmp_path: Path) -> None:
    found = found_in(
        tmp_path, "FOF_API_KEY=fof-key-123456\nDATAFORSEO_LOGIN=me\nDATAFORSEO_PASSWORD=pw-123456\n"
    )
    assert found["Forms On Fire"][2] == env_scan.FORMS_ON_FIRE
    assert found["DataForSEO"][2] == env_scan.DATAFORSEO
    assert found["Forms On Fire"][0].service == found["DataForSEO"][0].service == "http"
    check_connection_secret(InstanceType.HTTP, found["DataForSEO"][1])


def test_storage_keys_pick_the_right_provider(tmp_path: Path) -> None:
    found = found_in(
        tmp_path,
        f"R2_ACCESS_KEY_ID=k123456789012\nR2_SECRET_ACCESS_KEY=s123456789012\n"
        f"R2_ACCOUNT_ID={'a' * 32}\nR2_BUCKET=acme\n"
        "AWS_ACCESS_KEY_ID=AKIAFAKE0000\nAWS_SECRET_ACCESS_KEY=s234567890123\nAWS_REGION=eu-west-1\n"
        "BLOB_READ_WRITE_TOKEN=vercel_blob_rw_fake_1\n",
    )
    assert json.loads(found["Cloudflare R2"][1])["provider"] == "r2"
    assert json.loads(found["AWS S3"][1])["region"] == "eu-west-1"
    assert json.loads(found["Vercel Blob"][1])["provider"] == "vercel_blob"
    for label in ("Cloudflare R2", "AWS S3", "Vercel Blob"):
        check_connection_secret(InstanceType.S3, found[label][1])


@pytest.mark.parametrize(
    ("line", "label"),
    [
        ("STRIPE_SECRET_KEY=sk_live_fake123456", "Stripe"),
        ("PAYPAL_CLIENT_SECRET=fake123456", "PayPal"),
        ("SQUARE_ACCESS_TOKEN=fake123456", "Square"),
        ("HUBSPOT_ACCESS_TOKEN=fake123456", "HubSpot"),
        ("QBO_CLIENT_SECRET=fake123456", "QuickBooks"),
        ("XERO_CLIENT_SECRET=fake123456", "Xero"),
        ("WP_APP_PASSWORD=fake123456", "WordPress"),
    ],
)
def test_a_service_without_a_connection_is_named_and_cant_connect(
    tmp_path: Path, line: str, label: str
) -> None:
    finding = found_in(tmp_path, line + "\n")[label][0]
    assert not finding.usable
    assert "no " + label in finding.why
    assert "fake123456" not in finding.masked


# --- MCP servers in a project ---


def write(folder: Path, name: str, data: Any) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")


def test_servers_in_each_tools_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    project = tmp_path / "project"
    home.mkdir()
    project.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    write(project, ".mcp.json", {"mcpServers": {"one": {"url": "https://one.example/mcp"}}})
    write(project, ".cursor/mcp.json", {"mcpServers": {"two": {"url": "https://two.example/mcp"}}})
    write(project, ".vscode/mcp.json", {"servers": {"three": {"url": "https://three.example/mcp"}}})
    write(project, ".codex/config.toml", '[mcp_servers.four]\nurl = "https://four.example/mcp"\n')
    known = {str(project.resolve()): {"mcpServers": {"five": {"url": "https://five.example/mcp"}}}}
    write(home, ".claude.json", {"projects": known})
    found = {m[0].label: m[0] for m in project_servers.scan(project)}
    assert set(found) == {"one", "two", "three", "four", "five"}
    assert found["five"].file == "Claude Code's settings"
    assert all(f.service == "mcp" for f in found.values())


def test_the_same_server_in_two_files_shows_once(tmp_path: Path) -> None:
    server = {"mcpServers": {"one": {"url": "https://one.example/mcp"}}}
    write(tmp_path, ".mcp.json", server)
    write(tmp_path, ".cursor/mcp.json", server)
    assert len(project_servers.scan(tmp_path)) == 1


def test_a_temporary_tunnel_gets_a_warning(tmp_path: Path) -> None:
    write(
        tmp_path, ".mcp.json", {"mcpServers": {"dev": {"url": "https://a-b.trycloudflare.com/mcp"}}}
    )
    [(finding, _, _)] = project_servers.scan(tmp_path)
    assert finding.usable
    assert "temporary tunnel" in finding.warning


def test_an_address_on_this_computer_cant_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RINGKEEPER_ALLOW_LOCAL_CONNECTIONS", raising=False)
    write(tmp_path, ".mcp.json", {"mcpServers": {"dev": {"url": "https://localhost:3000/mcp"}}})
    [(finding, _, _)] = project_servers.scan(tmp_path)
    assert not finding.usable


def test_secrets_are_filled_in_from_env_and_never_shown(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("MY_KEY=real-secret-value\n", encoding="utf-8")
    server = {"url": "https://one.example/mcp", "headers": {"Authorization": "Bearer ${MY_KEY}"}}
    write(tmp_path, ".mcp.json", {"mcpServers": {"one": server}})
    [(finding, secret, _)] = project_servers.scan(tmp_path)
    assert json.loads(secret)["headers"]["Authorization"] == "Bearer real-secret-value"
    assert "real-secret-value" not in str(vars(finding))


def test_a_missing_variable_says_which(tmp_path: Path) -> None:
    server = {"url": "https://one.example/mcp", "headers": {"X-Key": "${NOT_THERE}"}}
    write(tmp_path, ".mcp.json", {"mcpServers": {"one": server}})
    [(finding, _, _)] = project_servers.scan(tmp_path)
    assert not finding.usable
    assert "NOT_THERE" in finding.why


def test_a_server_in_the_projects_code(tmp_path: Path) -> None:
    write(tmp_path, "src/server.py", "from fastmcp import FastMCP\nserver = FastMCP('acme')\n")
    write(tmp_path, "src/other.py", "print('hello')\n")
    [(finding, secret, _)] = project_servers.scan(tmp_path)
    assert finding.file == "src/server.py"
    spec = json.loads(secret)
    assert spec["command"] == "python"
    assert spec["args"][-1].endswith("server.py")


def test_connecting_a_found_server_links_it_to_the_project(
    owner: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write(tmp_path, ".mcp.json", {"mcpServers": {"one": {"url": "https://one.example/mcp"}}})
    def the_folder(_project: object) -> Path:
        return tmp_path

    monkeypatch.setattr("ringkeeper.api.convenience_routes._folder", the_folder)
    assert owner.post("/projects", json={"name": "acme-web", "owner": "acme"}).status_code == 201
    [found] = owner.get("/projects/acme-web/scan").json()["found"]
    made = owner.post(
        "/projects/acme-web/scan/connect",
        json={"id": found["id"], "env": "dev", "name": "acme-one", "title": "One"},
    )
    assert made.status_code == 201
    with session_scope() as session:
        instance = instances.get_instance(session, "acme-one")
        assert instance.type is InstanceType.MCP
        assert instances.bound_project_names(instance) == ["acme-web"]
        assert instance.config["preview"] == found["masked"]


# --- Logins on this computer ---


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.delenv("GH_CONFIG_DIR", raising=False)
    return tmp_path


def test_nothing_signed_in_finds_nothing(machine: Path) -> None:
    assert cli_logins.find() == []


def test_github_and_vercel_logins_can_connect(machine: Path) -> None:
    write(machine, ".config/gh/hosts.yml", "github.com:\n  user: octo\n  oauth_token: gho_fake\n")
    write(machine, ".local/share/com.vercel.cli/auth.json", {"token": "vercel-fake-token"})
    github, vercel = cli_logins.find()
    assert (github.tool, github.account, github.usable) == ("GitHub CLI", "octo", True)
    assert "gho_fake" not in str(vars(github))
    assert "vercel-fake-token" not in str(vars(vercel))
    assert cli_logins.token_of(github) == "gho_fake"
    assert cli_logins.token_of(vercel) == "vercel-fake-token"


def test_an_ended_vercel_login_says_so(machine: Path) -> None:
    write(machine, ".local/share/com.vercel.cli/auth.json", {"token": "t", "expiresAt": 1000})
    [login] = cli_logins.find()
    assert not login.usable
    assert "vercel login" in login.why


def test_the_clouds_are_listed_and_cant_connect_yet(machine: Path) -> None:
    write(machine, ".aws/config",
          "[default]\nregion=us-east-1\n[profile acme]\nsso_session=a\nregion=eu-west-1\n"
          "[sso-session a]\nsso_start_url=https://a.example\n")  # fmt: skip
    write(machine, ".aws/credentials", "[keysonly]\naws_access_key_id=AKIAFAKE\n")
    write(machine, ".azure/azureProfile.json", {"subscriptions": [{"name": "Acme sub", "id": "1"}]})
    write(machine, ".config/gcloud/configurations/config_default",
          "[core]\naccount=me@acme.example\nproject=acme-prod\n")  # fmt: skip
    write(machine, ".oci/config", "[DEFAULT]\ntenancy=ocid1.t\nregion=us-ashburn-1\n")
    found = cli_logins.find()
    accounts = {login.account for login in found}
    assert "acme (SSO, eu-west-1)" in accounts
    assert "keysonly (keys)" in accounts
    assert "Acme sub" in accounts
    assert "me@acme.example (acme-prod)" in accounts
    assert "DEFAULT (us-ashburn-1)" in accounts
    assert not any(login.usable for login in found)
    assert "AKIAFAKE" not in str(found)


def test_connecting_a_login_makes_it_your_own_and_asks_for_changes(
    owner: TestClient, machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ringkeeper.api.convenience_routes._on_this_computer", lambda: True)
    write(machine, ".config/gh/hosts.yml", "github.com:\n  user: octo\n  oauth_token: gho_fake\n")
    [login] = owner.get("/logins").json()["found"]
    assert "gho_fake" not in str(login)
    made = owner.post(
        "/logins/connect", json={"id": login["id"], "name": "my-github", "title": login["title"]}
    )
    assert made.status_code == 201
    with session_scope() as session:
        instance = instances.get_instance(session, "my-github")
        assert instance.shared
        assert instance.env is Env.PROD
        assert instance.bindings == []  # you choose the projects
    assert owner.get("/logins").json()["found"][0]["added"] is True


def test_a_skipped_login_isnt_offered_again(
    owner: TestClient, machine: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("ringkeeper.api.convenience_routes._on_this_computer", lambda: True)
    write(machine, ".config/gh/hosts.yml", "github.com:\n  user: octo\n  oauth_token: gho_fake\n")
    [login] = owner.get("/logins").json()["found"]
    assert owner.post("/logins/skip", json={"id": login["id"]}).status_code == 204
    assert owner.get("/logins").json()["found"] == []
