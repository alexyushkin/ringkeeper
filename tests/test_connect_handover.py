"""What Ringkeeper Connect sends with Send to Ringkeeper (services.connect_handover): opened
with the key only this Ringkeeper has, and brought in as clients, projects and connections."""

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, Project, ProjectInstance
from ringkeeper.services import claude_desktop, connect_handover
from ringkeeper.vault import get_vault
from tests.helpers import make_project

FIXTURE = Path(__file__).parent / "fixtures" / "from_connect"
pytestmark = pytest.mark.usefixtures("database_url")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def seal(payload: dict[str, Any], public: bytes) -> str:
    """What Connect's sealForRingkeeper does (packages/gateway/src/store/handover.ts)."""
    mine = X25519PrivateKey.generate()
    shared = mine.exchange(X25519PublicKey.from_public_bytes(public))
    salt, iv = os.urandom(16), os.urandom(12)
    key = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt, info=connect_handover.CONTEXT
    ).derive(shared)
    sealed = AESGCM(key).encrypt(iv, json.dumps(payload).encode(), connect_handover.CONTEXT)
    epk = mine.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return json.dumps({
        "kind": connect_handover.KIND, "version": 1, "createdAt": payload.get("createdAt"),
        "epk": b64(epk), "salt": b64(salt), "iv": b64(iv), "tag": b64(sealed[-16:]),
        "data": b64(sealed[:-16]),
    })  # fmt: skip


def send(payload: dict[str, Any], name: str = "from-connect-20261122T120000.json") -> Path:
    """Publish this Ringkeeper's key, then leave a payload sealed with it, as Connect does."""
    settings = get_settings()
    public = connect_handover.publish(settings, settings.data_dir).read_text().strip()
    path = connect_handover.folder(settings.data_dir) / name
    path.write_text(seal(payload, base64.urlsafe_b64decode(public + "=")), encoding="utf-8")
    return path


def payload(**more: Any) -> dict[str, Any]:
    python = sys.executable
    return {
        "kind": "ringkeeper-connect-clients", "version": 1, "createdAt": "2026-11-22T12:00:00Z",
        "clients": [
            {
                "name": "Acme Bakery",
                "connections": [
                    {"id": "notes", "name": "Notes", "kind": "local", "command": python,
                     "args": ["-V"], "fields": {"NOTES_KEY": {"secret": "fake-notes-key-1"},
                                                "NOTES_REGION": {"value": "eu"}}},
                    {"id": "tickets", "name": "Tickets", "kind": "remote",
                     "url": "https://tickets.example/mcp",
                     "fields": {"X-Api-Key": {"secret": "fake-ticket-key-2"}}},
                    {"id": "crm", "name": "CRM", "kind": "remote", "auth": "oauth",
                     "url": "https://crm.example/mcp", "fields": {}},
                    {"id": "old", "name": "Old", "kind": "local", "command": python, "off": True},
                ],
            },
            {"name": "Gone Co", "archived": True, "connections": []},
        ],
        "everyClient": [{"id": "fetch", "name": "Fetch", "kind": "local", "command": python,
                         "args": ["-V"], "fields": {}}],
        "ownApps": [],
        **more,
    }  # fmt: skip


def test_the_key_is_published_once_and_kept() -> None:
    settings = get_settings()
    first = connect_handover.publish(settings, settings.data_dir).read_text()
    again = connect_handover.publish(settings, settings.data_dir).read_text()
    assert first == again
    assert len(base64.urlsafe_b64decode(first.strip() + "=")) == 32
    assert get_vault(settings).resolve(connect_handover.KEY_REF).reveal()


def test_what_connect_itself_sealed_opens_here(monkeypatch: pytest.MonkeyPatch) -> None:
    # Sealed by Connect's own code (handover.ts) for a key made with it: the two agree.
    settings = get_settings()
    key = (FIXTURE / "private-key.txt").read_text().strip()
    get_vault(settings).store(connect_handover.KEY_REF, key)
    monkeypatch.setattr(claude_desktop, "missing_command", lambda _command: None)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    sent = connect_handover.open_sent(settings, FIXTURE / "from-connect-20261122T100000.json")
    assert [c["name"] for c in sent["clients"]] == ["Acme Bakery", "Old Client"]
    with session_scope() as session:
        shown = connect_handover.summary(session, sent)
    [acme] = shown["clients"]  # the archived one stays behind
    assert acme["new_project"] == "acme-bakery"
    connections = {c["name"]: c for c in acme["connections"]}
    assert connections["Notes"]["secrets"] == 1
    assert connections["Tickets"]["transport"] == "url"
    assert "sign in to it again here" in connections["CRM"]["problem"]
    assert [t["name"] for t in shown["every_client"]] == ["Fetch"]
    assert "fake-notes-key" not in json.dumps(shown)


def test_everything_comes_in_with_its_keys_in_the_vault() -> None:
    path = send(payload())
    settings = get_settings()
    with session_scope() as session:
        made = connect_handover.bring_in(
            session, settings, connect_handover.open_sent(settings, path)
        )
    assert sorted(made["connections"]) == ["fetch", "notes", "tickets"]
    assert made["clients"] == ["Acme Bakery"]
    assert made["projects"] == ["acme-bakery"]
    assert made["left"] == [
        "Acme Bakery: CRM (its sign-in was made in Ringkeeper Connect: sign in to it again "
        "here, from Connectors > Browse connectors)"
    ]
    with session_scope() as session:
        rows = {i.name: i for i in session.scalars(select(Instance))}
        project = session.scalars(select(Project).where(Project.name == "acme-bakery")).one()
        assert project.title == "Acme Bakery"
        notes = rows["notes"]
        assert (notes.owner, notes.env) == (project.owner, Env.PROD)  # a client's: held
        assert rows["fetch"].shared  # for every client
        assert rows["fetch"].env is Env.DEV
        linked = {b.instance.name for b in session.scalars(select(ProjectInstance))}
        assert {"notes", "tickets"} <= linked  # and Fetch, a shared tool for every project
        spec = json.loads(get_vault(settings).resolve(notes.secret_ref or "").reveal())
        assert spec["env"] == {"NOTES_KEY": "fake-notes-key-1", "NOTES_REGION": "eu"}
        tickets = json.loads(get_vault(settings).resolve(rows["tickets"].secret_ref or "").reveal())
        assert tickets["headers"] == {"X-Api-Key": "fake-ticket-key-2"}


def test_sending_again_brings_in_only_what_s_new() -> None:
    settings = get_settings()
    with session_scope() as session:
        connect_handover.bring_in(
            session, settings, connect_handover.open_sent(settings, send(payload()))
        )
    again = connect_handover.open_sent(settings, send(payload(), "from-connect-2.json"))
    with session_scope() as session, pytest.raises(RuleViolationError, match="nothing new"):
        connect_handover.bring_in(session, settings, again)


def test_a_client_that_s_here_keeps_its_projects() -> None:
    with session_scope() as session:
        make_project(session, "bakery-site", owner="Acme Bakery")
    settings = get_settings()
    with session_scope() as session:
        made = connect_handover.bring_in(
            session, settings, connect_handover.open_sent(settings, send(payload()))
        )
    assert made["clients"] == []
    assert made["projects"] == []
    with session_scope() as session:
        linked = {
            (b.project.name, b.instance.name) for b in session.scalars(select(ProjectInstance))
        }
    assert ("bakery-site", "notes") in linked


def test_what_was_changed_or_locked_for_another_ringkeeper_is_refused() -> None:
    path = send(payload())
    sealed = json.loads(path.read_text())
    sealed["data"] = sealed["data"][:-4] + (
        "AAAA" if not sealed["data"].endswith("AAAA") else "BBBB"
    )
    path.write_text(json.dumps(sealed))
    settings = get_settings()
    with pytest.raises(connect_handover.HandoverError, match="locked for another Ringkeeper"):
        connect_handover.open_sent(settings, path)
    other = X25519PrivateKey.generate().public_key()
    elsewhere = connect_handover.folder(settings.data_dir) / "from-connect-other.json"
    raw = other.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    elsewhere.write_text(seal(payload(), raw))
    with pytest.raises(connect_handover.HandoverError, match="locked for another Ringkeeper"):
        connect_handover.open_sent(settings, elsewhere)
    (connect_handover.folder(settings.data_dir) / "from-connect-junk.json").write_text("{}")
    with pytest.raises(connect_handover.HandoverError, match="isn't something"):
        connect_handover.open_sent(
            settings, connect_handover.folder(settings.data_dir) / "from-connect-junk.json"
        )
