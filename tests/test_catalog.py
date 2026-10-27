"""The open catalog (M7): MCP Registry search (a fake registry), signing in to a
catalog server (a fake OAuth server), adding a server by its address,
installing a pinned local package (a fake npm), and the generic rules for
servers without built-in ones: unannotated tools are held."""

import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from fastapi.testclient import TestClient

from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import Target, ToolKind
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env
from ringkeeper.services import catalog, instances, local_servers, mcp_oauth
from ringkeeper.vault import Secret, get_vault
from tests import test_web_api

# The signed-in owner's client, as in the web API's tests.
setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

REGISTRY = "https://registry.modelcontextprotocol.io/v0.1/servers"
NOTES = "https://mcp.notes.example/mcp"
NOTES_AUTH = "https://auth.notes.example"
WEATHER = "https://weather.example/mcp"


def entry(name: str, version: str = "1.0.0", **server: Any) -> dict[str, Any]:
    official = {"status": "active", "isLatest": True}
    return {
        "server": {"name": name, "version": version, "description": f"The {name} server", **server},
        "_meta": {"io.modelcontextprotocol.registry/official": official},
    }


def files_package(version: str) -> dict[str, Any]:
    return {
        "registryType": "npm", "identifier": "@bob/files-mcp", "version": version,
        "transport": {"type": "stdio"},
        "environmentVariables": [
            {"name": "FILES_ROOT", "description": "The folder to share", "isRequired": True}
        ],
    }  # fmt: skip


class FakeWorld:
    """The MCP Registry, and the servers it lists (one offers sign-in)."""

    def __init__(self) -> None:
        self.online = True
        self.files_version = "1.2.3"
        self.registered: list[dict[str, Any]] = []
        self.challenge: str | None = None

    def servers(self) -> list[dict[str, Any]]:
        return [
            entry("com.example.notes/notes", title="Notes",
                  remotes=[{"type": "streamable-http", "url": NOTES}]),
            entry("io.github.alice/weather", remotes=[{
                "type": "streamable-http", "url": WEATHER,
                "headers": [{"name": "X-Api-Key", "isRequired": True, "isSecret": True}],
            }]),
            entry("tech.neon/neon", remotes=[{"type": "streamable-http", "url": "https://mcp.neon.tech/mcp"}]),
            entry("io.github.bob/files", self.files_version,
                  packages=[files_package(self.files_version)]),
            entry("io.github.eve/loose",
                  packages=[{**files_package("latest"), "identifier": "loose"}]),
        ]  # fmt: skip

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        if not self.online:
            raise httpx2.ConnectError("offline", request=request)
        url = str(request.url).split("?", 1)[0]
        if url == REGISTRY:
            words = request.url.params.get("search", "").lower().split()
            found = [
                s for s in self.servers()
                if all(w in json.dumps(s["server"]).lower() for w in words)
            ]  # fmt: skip
            return httpx2.Response(200, json={"servers": found, "metadata": {"count": len(found)}})
        if url.startswith(f"{REGISTRY}/") and url.endswith("/versions/latest"):
            name = url.removeprefix(f"{REGISTRY}/").removesuffix("/versions/latest")
            name = name.replace("%2F", "/")
            for s in self.servers():
                if s["server"]["name"] == name:
                    return httpx2.Response(200, json=s)
            return httpx2.Response(404)
        if url == "https://mcp.notes.example/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(
                200, json={"resource": NOTES, "authorization_servers": [NOTES_AUTH]}
            )
        if url == f"{NOTES_AUTH}/.well-known/oauth-authorization-server":
            return httpx2.Response(200, json={
                "issuer": NOTES_AUTH, "authorization_endpoint": f"{NOTES_AUTH}/authorize",
                "token_endpoint": f"{NOTES_AUTH}/token",
                "registration_endpoint": f"{NOTES_AUTH}/register",
            })  # fmt: skip
        if url == f"{NOTES_AUTH}/register":
            self.registered.append(json.loads(request.content))
            return httpx2.Response(201, json={"client_id": "rk-notes"})
        if url == f"{NOTES_AUTH}/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["resource"] == NOTES
            return httpx2.Response(200, json={"access_token": "notes-token", "expires_in": 3600})
        return httpx2.Response(404)


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> FakeWorld:
    fake = FakeWorld()
    handler = httpx2.MockTransport(fake.handle)
    monkeypatch.setattr(catalog, "transport", handler)
    monkeypatch.setattr(mcp_oauth, "transport", handler)
    monkeypatch.setenv("RINGKEEPER_WEB_ORIGIN", "https://rk.example")
    return fake


def by_name(found: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {s["name"]: s for s in found["servers"]}


def test_the_catalog_searches_the_registry_and_works_offline_from_its_cache(
    owner: TestClient, world: FakeWorld
) -> None:
    found = by_name(owner.get("/catalog").json())
    notes, weather = found["com.example.notes/notes"], found["io.github.alice/weather"]
    assert (notes["title"], notes["publisher"], notes["verified"]) == (
        "Notes", "notes.example.com", "domain",
    )  # fmt: skip
    assert (weather["publisher"], weather["verified"]) == ("github.com/alice", "github")
    assert (notes["kind"], notes["signin"]) == ("remote", True)  # asked the server
    assert weather["signin"] is False  # it wants an API key header instead
    assert weather["remotes"][0]["headers"][0]["name"] == "X-Api-Key"
    assert found["tech.neon/neon"]["recommended"] == "neon"  # built-in rules apply
    files = found["io.github.bob/files"]
    assert (files["kind"], files["packages"][0]["identifier"]) == ("local", "@bob/files-mcp")
    assert found["com.example.notes/notes"]["recommended"] is None

    only = by_name(owner.get("/catalog", params={"q": "weather"}).json())
    assert list(only) == ["io.github.alice/weather"]

    world.online = False
    offline = owner.get("/catalog", params={"q": "notes"}).json()
    assert offline["offline"] is True
    assert [s["name"] for s in offline["servers"]] == ["com.example.notes/notes"]
    assert offline["servers"][0]["signin"] is True  # remembered too
    assert owner.get("/catalog/servers/io.github.bob/files").json()["version"] == "1.2.3"


def test_signing_in_to_a_catalog_server_gives_it_the_generic_rules(
    owner: TestClient, world: FakeWorld
) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    body = {
        "server": "com.example.notes/notes", "url": NOTES, "name": "acme-notes",
        "owner": "acme", "env": "prod", "project": "acme-web",
    }  # fmt: skip
    narrowed = owner.post("/connections/signin", json={**body, "scope": {"project": "x"}})
    assert narrowed.status_code == 409  # only services with built-in rules narrow
    started = owner.post("/connections/signin", json=body)
    assert started.status_code == 200, started.text
    page_url = str(started.json()["url"])
    query = {k: v[0] for k, v in parse_qs(urlsplit(page_url).query).items()}
    assert query["resource"] == NOTES
    assert world.registered[0]["client_name"] == "Ringkeeper"
    page = owner.get("/oauth/callback", params={"state": query["state"], "code": "any"})
    assert page.status_code == 200, page.text

    [connection] = [c for c in owner.get("/connections").json() if c["name"] == "acme-notes"]
    assert connection["env"] == "prod"  # Test or Production: chosen when connecting
    assert connection["config"]["registry"] == "com.example.notes/notes"
    assert "remote" not in connection["config"]
    assert [link["project"] for link in connection["links"]] == ["acme-web"]

    # Generic rules: annotations decide; a tool without them is held.
    config = {
        **connection["config"],
        "annotations": {
            "search": {"read_only": True, "destructive": None},
            "append": {"read_only": None, "destructive": None},
            "archive": {"read_only": False, "destructive": True},
        },
    }
    assert mcp.rule(config, "search").level == mcp.READ
    assert mcp.rule(config, "append") == mcp.Rule(mcp.APPROVE, mcp.BY_DEFAULT)
    assert mcp.rule(config, "never-listed").level == mcp.APPROVE
    assert mcp.rule(config, "archive").level == mcp.CONFIRM


def test_unannotated_tools_are_held_at_the_gateway() -> None:
    annotations = {
        "append": {"read_only": None, "destructive": None},
        "search": {"read_only": True, "destructive": None},
    }
    target = Target(
        name="acme-notes", env=Env.DEV, base_url=None, secret=Secret("ref", "{}"),
        config={"registry": "com.example.notes/notes", "annotations": annotations},
    )  # fmt: skip
    classify = mcp.call_tool.classify
    assert classify is not None
    assert classify(target, {"tool": "append"}) is ToolKind.WRITE  # waits for approval
    assert classify(target, {"tool": "unknown-tool"}) is ToolKind.WRITE
    assert classify(target, {"tool": "search"}) is ToolKind.READ


def test_adding_a_server_by_its_address(owner: TestClient, world: FakeWorld) -> None:
    probed = owner.post("/catalog/probe", json={"url": NOTES}).json()
    assert (probed["signin"], probed["recommended"]) == (True, None)
    assert owner.post("/catalog/probe", json={"url": WEATHER}).json()["signin"] is False
    neon = owner.post("/catalog/probe", json={"url": "https://mcp.neon.tech/mcp/"}).json()
    assert neon["recommended"] == "neon"
    assert owner.post("/catalog/probe", json={"url": "ftp://files.example/mcp"}).status_code == 409

    body = {
        "url": WEATHER,
        "name": "acme-weather",
        "owner": "acme",
        "headers": {"X-Api-Key": "k-123456"},
    }
    assert owner.post("/connections/remote", json=body).status_code == 201, body
    with session_scope() as session:
        stored = instances.get_instance(session, "acme-weather")
        secret = json.loads(get_vault().resolve(stored.secret_ref).reveal())
        assert stored.config == {"source": "headers", "preview": WEATHER}
    assert secret == {"url": WEATHER, "headers": {"X-Api-Key": "k-123456"}}

    # A known service's address connects as that service, with its rules.
    token = {
        "url": "https://mcp.neon.tech/mcp",
        "name": "acme-neon",
        "owner": "acme",
        "token": "napi_x",
    }
    assert owner.post("/connections/remote", json=token).status_code == 201
    with session_scope() as session:
        assert instances.get_instance(session, "acme-neon").config["remote"] == "neon"


class FakeNpm:
    """Lays out what `npm install --prefix` would, and remembers what was asked."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], cwd: Path) -> str:
        self.calls.append(args)
        prefix = Path(args[args.index("--prefix") + 1])
        identifier, version = args[-1].rsplit("@", 1)
        package = prefix / "node_modules" / Path(*identifier.split("/"))
        (package / "bin").mkdir(parents=True)
        (package / "bin" / "server.js").write_text("// the server\n")
        manifest = {"name": identifier, "version": version, "bin": {"files-mcp": "bin/server.js"}}
        (package / "package.json").write_text(json.dumps(manifest))
        return ""


def wait_installed(owner: TestClient, name: str) -> dict[str, Any]:
    deadline = time.monotonic() + 10
    while (status := owner.get(f"/connections/local/{name}").json())["state"] == "installing":
        assert time.monotonic() < deadline
        time.sleep(0.05)
    return status


def test_a_local_server_installs_pinned_after_one_confirmation(
    owner: TestClient, world: FakeWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    npm = FakeNpm()
    monkeypatch.setattr(local_servers, "runner", npm)

    def found(program: str) -> str:
        return f"C:/tools/{program}"

    monkeypatch.setattr(local_servers, "finder", found)
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})

    plan = owner.post("/catalog/plan", json={"server": "io.github.bob/files"}).json()
    assert (plan["publisher"], plan["verified"]) == ("github.com/bob", "github")
    assert (plan["identifier"], plan["version"], plan["missing"]) == (
        "@bob/files-mcp",
        "1.2.3",
        False,
    )
    assert "--ignore-scripts" in plan["install"]
    body = {
        "server": "io.github.bob/files", "fingerprint": plan["fingerprint"], "name": "acme-files",
        "owner": "acme", "project": "acme-web", "values": {"FILES_ROOT": "C:/shared"},
    }  # fmt: skip
    stale = owner.post("/connections/local", json={**body, "fingerprint": "0" * 32})
    assert stale.status_code == 409  # not what was confirmed
    missing = owner.post("/connections/local", json={**body, "values": {}})
    assert missing.status_code == 409
    assert "FILES_ROOT" in missing.text
    assert npm.calls == []  # nothing ran before the confirmation matched

    assert owner.post("/connections/local", json=body).status_code == 202
    assert wait_installed(owner, "acme-files") == {
        "name": "acme-files",
        "state": "done",
        "error": None,
    }
    [call] = npm.calls
    assert call[-1] == "@bob/files-mcp@1.2.3"
    assert "--ignore-scripts" in call
    folder = get_settings().data_dir / "mcp-packages" / "npm" / "@bob__files-mcp@1.2.3"
    with session_scope() as session:
        connection = instances.get_instance(session, "acme-files")
        spec = json.loads(get_vault().resolve(connection.secret_ref).reveal())
        assert connection.config["package"] == {
            "registry": "npm", "identifier": "@bob/files-mcp", "version": "1.2.3",
            "pinned": "@bob/files-mcp@1.2.3",
        }  # fmt: skip
        assert [b.project.name for b in connection.bindings] == ["acme-web"]
    assert spec["command"] == "C:/tools/node"
    assert Path(spec["args"][0]) == (folder / "node_modules/@bob/files-mcp/bin/server.js").resolve()
    assert spec["env"] == {"FILES_ROOT": "C:/shared"}

    # A newer version is offered, never installed by itself.
    world.files_version = "1.3.0"
    owner.get("/catalog", params={"q": "files"})
    updates = owner.get("/catalog/updates").json()
    assert updates == [{"connection": "acme-files", "server": "io.github.bob/files",
                        "installed": "1.2.3", "latest": "1.3.0"}]  # fmt: skip
    assert len(npm.calls) == 1
    newer = owner.post("/catalog/plan", json={"server": "io.github.bob/files"}).json()
    update = {**body, "fingerprint": newer["fingerprint"], "replace": "acme-files", "project": None}
    assert owner.post("/connections/local", json=update).status_code == 202
    assert wait_installed(owner, "acme-files")["state"] == "done"
    assert not folder.exists()  # the old version's copy is gone
    with session_scope() as session:
        assert instances.get_instance(session, "acme-files").config["package"]["version"] == "1.3.0"

    assert owner.delete("/connections/acme-files", params={"force": True}).status_code == 204
    assert not (folder.parent / "@bob__files-mcp@1.3.0").exists()


def test_a_package_without_an_exact_version_isnt_installed(
    owner: TestClient, world: FakeWorld
) -> None:
    refused = owner.post("/catalog/plan", json={"server": "io.github.eve/loose"})
    assert refused.status_code == 409
    assert "exact version" in refused.text
