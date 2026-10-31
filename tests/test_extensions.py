"""Installing a desktop extension from its file (.mcpb): the manifest shown for one
confirmation, the same file installed into the data folder, the connection made
with the same rules as an extension imported from Claude Desktop, and files that
aren't installed (not a bundle, unsafe paths, another platform, too big)."""

import hashlib
import io
import json
import sys
import zipfile
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import Env
from ringkeeper.services import bundles, instances
from ringkeeper.vault import get_vault
from tests import test_catalog, test_web_api

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
wait_installed = test_catalog.wait_installed

NOTES: dict[str, Any] = {
    "manifest_version": "0.3",
    "name": "notes-desk",
    "display_name": "Notes Desk",
    "version": "1.4.0",
    "description": "Search and add notes.",
    "author": {"name": "Dana Example", "url": "https://notes.example"},
    "server": {
        "type": "node",
        "entry_point": "server/index.js",
        "mcp_config": {
            "command": "node",
            "args": ["${__dirname}/server/index.js", "--root", "${user_config.root}"],
            "env": {"NOTES_KEY": "${user_config.api_key}", "MODE": "${user_config.mode}"},
        },
    },
    "tools": [
        {"name": "search_notes", "description": "Search the notes"},
        {"name": "add_note", "description": "Add a note"},
    ],
    "user_config": {
        "api_key": {"type": "string", "title": "API key", "sensitive": True, "required": True},
        "root": {"type": "directory", "title": "Notes folder", "default": "${HOME}/Notes"},
        "mode": {"type": "string", "title": "Mode"},
    },
}


def bundle(manifest: dict[str, Any] | None, files: dict[str, str] | None = None) -> bytes:
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        if manifest is not None:
            z.writestr("manifest.json", json.dumps(manifest))
        for name, text in (files or {"server/index.js": "// the server"}).items():
            z.writestr(name, text)
    return data.getvalue()


def upload(owner: TestClient, content: bytes) -> Any:
    headers = {"content-type": "application/octet-stream"}
    return owner.post("/catalog/extensions", content=content, headers=headers)


def node_only(command: str) -> str | None:
    return None if command == "node" else f"runs {command}, which isn't on this computer"


@pytest.fixture(autouse=True)
def _node_here(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bundles, "missing_command", node_only)


def test_an_extension_installs_from_its_file_after_one_confirmation(owner: TestClient) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    content = bundle(NOTES, {"server/index.js": "// the server", "server/lib/db.js": "// db"})

    shown = upload(owner, content).json()
    assert shown["fingerprint"] == hashlib.sha256(content).hexdigest()
    assert (shown["title"], shown["version"], shown["publisher"]) == (
        "Notes Desk",
        "1.4.0",
        "Dana Example",
    )
    assert [t["name"] for t in shown["tools"]] == ["search_notes", "add_note"]
    assert shown["runs"] == "node <extension folder>/server/index.js --root <Notes folder>"
    assert shown["problem"] is None
    assert [(s["key"], s["required"], s["secret"]) for s in shown["settings"]] == [
        ("api_key", True, True),
        ("root", False, False),
        ("mode", False, False),
    ]
    staged = bundles.staged(get_settings(), shown["fingerprint"])
    assert staged.is_file()

    body = {"fingerprint": shown["fingerprint"], "name": "acme-notes", "owner": "acme",
            "project": "acme-web", "values": {"api_key": "secret-notes-key"}}  # fmt: skip
    gone = owner.post("/connections/extension", json={**body, "fingerprint": "0" * 64})
    assert gone.status_code == 409  # not the file that was shown
    missing = owner.post("/connections/extension", json={**body, "values": {}})
    assert missing.status_code == 409
    assert "API key" in missing.text
    pinned = f"notes-desk@1.4.0+{shown['fingerprint'][:12]}"
    folder = get_settings().data_dir / "mcp-packages" / "mcpb" / pinned
    assert not folder.exists()  # nothing unpacked before the confirmation

    assert owner.post("/connections/extension", json=body).status_code == 202
    assert wait_installed(owner, "acme-notes")["state"] == "done"
    assert (folder / "server" / "index.js").is_file()
    assert (folder / "server" / "lib" / "db.js").is_file()
    assert not staged.exists()  # the upload is gone once installed
    with session_scope() as session:
        connection = instances.get_instance(session, "acme-notes")
        spec = json.loads(get_vault().resolve(connection.secret_ref).reveal())
        # The same rules as an imported extension: Test data, generic tool rules.
        assert connection.env is Env.DEV
        assert connection.config["source"] == "mcpb"
        assert connection.config["package"]["pinned"] == pinned
        assert [b.project.name for b in connection.bindings] == ["acme-web"]
        assert "secret-notes-key" not in json.dumps(connection.config)
    assert spec["command"] == "node"
    assert Path(spec["args"][0]) == folder / "server" / "index.js"
    assert spec["args"][1:] == ["--root", f"{Path.home()}/Notes"]  # the default, filled in
    assert spec["env"] == {"NOTES_KEY": "secret-notes-key"}  # Mode was left empty

    assert owner.delete("/connections/acme-notes", params={"force": True}).status_code == 204
    assert not folder.exists()


def test_files_that_arent_installed(owner: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    owner.post("/projects", json={"name": "acme-web", "owner": "acme"})
    not_zip = upload(owner, b"just some text")
    assert not_zip.status_code == 409
    assert "doesn't open" in not_zip.text
    no_manifest = upload(owner, bundle(None))
    assert no_manifest.status_code == 409
    assert "manifest.json" in no_manifest.text
    assert not list(bundles.uploads(get_settings()).glob("*"))  # neither was kept

    escaping = upload(owner, bundle(NOTES, {"server/index.js": "", "../evil.txt": "x"})).json()
    assert escaping["problem"] == "it has a file that would land outside its folder"
    body = {"fingerprint": escaping["fingerprint"], "name": "acme-notes", "owner": "acme",
            "values": {"api_key": "k"}}  # fmt: skip
    refused = owner.post("/connections/extension", json=body)
    assert refused.status_code == 409
    assert not (get_settings().data_dir / "mcp-packages" / "evil.txt").exists()

    elsewhere = "linux" if sys.platform != "linux" else "darwin"
    foreign = upload(owner, bundle({**NOTES, "compatibility": {"platforms": [elsewhere]}})).json()
    assert foreign["problem"].startswith("it's made for")

    own = {"type": "binary", "mcp_config": {"command": "${__dirname}/bin/notes"}}
    binary = {**NOTES, "server": own}
    without = upload(owner, bundle(binary)).json()
    assert without["problem"] == "the program it starts isn't in the file"
    with_it = upload(owner, bundle(binary, {"bin/notes": "#!/bin/sh"})).json()
    assert with_it["problem"] is None

    python = {**NOTES, "server": {"type": "python", "mcp_config": {"command": "pythonx"}}}
    needs = upload(owner, bundle(python)).json()["problem"]
    assert needs == "runs pythonx, which isn't on this computer"

    monkeypatch.setattr(bundles, "MAX_BYTES", 100)
    assert upload(owner, bundle(NOTES)).status_code == 413


def test_an_extension_can_be_a_shared_tool_for_every_client(owner: TestClient) -> None:
    for name, client in (("acme-web", "acme"), ("globex-app", "globex")):
        owner.post("/projects", json={"name": name, "owner": client})
    shown = upload(owner, bundle(NOTES, {"server/index.js": "// the server"})).json()
    body = {"fingerprint": shown["fingerprint"], "name": "notes-desk", "owner": "acme",
            "project": "acme-web", "values": {"api_key": "secret-notes-key"},
            "shared": True, "all_projects": True}  # fmt: skip
    assert owner.post("/connections/extension", json=body).status_code == 202
    assert wait_installed(owner, "notes-desk")["state"] == "done"
    [tool] = [c for c in owner.get("/connections").json() if c["name"] == "notes-desk"]
    assert (tool["shared"], tool["all_projects"], tool["owner"]) == (True, True, "you")
    assert tool["projects"] == ["acme-web", "globex-app"]
    owner.post("/projects", json={"name": "initech-site", "owner": "initech"})
    [tool] = [c for c in owner.get("/connections").json() if c["name"] == "notes-desk"]
    assert tool["projects"] == ["acme-web", "globex-app", "initech-site"]
    # Changing it keeps what it is: its package and source stay (they used to be dropped).
    changed = owner.patch("/connections/notes-desk", json={"env": "staging"}).json()
    assert (changed["env"], changed["config"]["source"], changed["shared"]) == (
        "staging", "mcpb", True,
    )  # fmt: skip
    # Any kind of connection can be yours: a database too, made shared after the fact.
    made = owner.post(
        "/connections",
        json={"name": "acme-db", "type": "supabase", "env": "dev", "owner": "acme",
              "secret": "postgresql://u:p@db.example/acme"},
    )  # fmt: skip
    assert made.status_code == 201, made.text
    shared = owner.patch("/connections/acme-db", json={"shared": True})
    assert shared.status_code == 200, shared.text
    assert (shared.json()["owner"], shared.json()["shared"]) == ("you", True)
