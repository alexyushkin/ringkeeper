"""What Ringkeeper Connect sends with its Send to Ringkeeper button: its clients, each with its
connections and their keys, and its tools for every client.

The desktop app keeps a key pair for this. The private key is in the vault; the public one is
in the data folder (handover/ringkeeper.pub), where Connect looks for it. Connect locks what it
sends with that public key (X25519, HKDF-SHA256, AES-256-GCM) and leaves it in the same folder,
so the keys are never on disk in the clear and there's no file to pick or password to type.

Bringing it in makes each client (and a project for one that has none here), each connection
as an MCP connection of that client linked to its projects, and each tool for every client as
a shared tool for all projects. A client's connection starts as Production: its changes wait
for approval. What can't come over is listed with why: a sign-in made in Connect (sign in again
here), a server that needs a program this computer doesn't have, an SSE server.
"""

import base64
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType, Project
from ringkeeper.schemas import BindingIn, InstanceIn, ProjectIn
from ringkeeper.services import (
    admin_audit,
    bindings,
    claude_desktop,
    clients,
    instances,
    projects,
    secrets_in,
)
from ringkeeper.vault import VaultError, get_vault

FOLDER = "handover"
PUBLIC = "ringkeeper.pub"
KEY_REF = "file://ringkeeper/connect-handover-key"
KIND = "ringkeeper-connect-handover"
PAYLOAD = "ringkeeper-connect-clients"
CONTEXT = b"ringkeeper-connect-handover 1"  # HKDF info and the cipher's associated data
SOURCE = "ringkeeper-connect"
MOST_BYTES = 20_000_000
JSON = dict[str, Any]


class HandoverError(Exception):
    """Said to the user as it is."""


def folder(data_dir: Path) -> Path:
    return data_dir / FOLDER


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _private(settings: Settings) -> X25519PrivateKey:
    """The private key, made the first time."""
    vault = get_vault(settings)
    try:
        return X25519PrivateKey.from_private_bytes(_unb64(vault.resolve(KEY_REF).reveal().strip()))
    except (VaultError, ValueError):
        key = X25519PrivateKey.generate()
        raw = key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        vault.store(KEY_REF, _b64(raw))
        return key


def publish(settings: Settings, data_dir: Path) -> Path:
    """Put the public key where Connect looks for it. Run when the desktop app starts."""
    raw = (
        _private(settings)
        .public_key()
        .public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )
    path = folder(data_dir) / PUBLIC
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file() or path.read_text(encoding="utf-8").strip() != _b64(raw):
        path.write_text(_b64(raw) + "\n", encoding="utf-8")
    return path


def waiting(data_dir: Path) -> list[Path]:
    """What Connect has left here, oldest first."""
    found = folder(data_dir).glob("from-connect-*.json")
    return sorted((p for p in found if p.is_file()), key=lambda p: p.name)


def open_sent(settings: Settings, path: Path) -> JSON:
    """What one file holds, unlocked. Raises HandoverError when it can't be opened."""
    try:
        if path.stat().st_size > MOST_BYTES:
            raise HandoverError("It's too big to be what Ringkeeper Connect sends.")
        sealed: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HandoverError("It can't be read. Send it again from Ringkeeper Connect.") from None
    if not isinstance(sealed, dict):
        raise HandoverError("It isn't something Ringkeeper Connect sent. Send it again.")
    sealed = cast(JSON, sealed)
    if sealed.get("kind") != KIND or sealed.get("version") != 1:
        raise HandoverError("It isn't something Ringkeeper Connect sent. Send it again.")
    try:
        theirs = X25519PublicKey.from_public_bytes(_unb64(str(sealed["epk"])))
        shared = _private(settings).exchange(theirs)
        key = HKDF(
            algorithm=hashes.SHA256(), length=32, salt=_unb64(str(sealed["salt"])), info=CONTEXT
        ).derive(shared)
        data = _unb64(str(sealed["data"])) + _unb64(str(sealed["tag"]))
        plain = AESGCM(key).decrypt(_unb64(str(sealed["iv"])), data, CONTEXT)
        payload: Any = json.loads(plain.decode("utf-8"))
    except (KeyError, ValueError, InvalidTag):
        raise HandoverError(
            "It was locked for another Ringkeeper, or it changed on the way. Send it again "
            "from Ringkeeper Connect, with this Ringkeeper open."
        ) from None
    if not isinstance(payload, dict) or cast(JSON, payload).get("kind") != PAYLOAD:
        raise HandoverError("It isn't something Ringkeeper Connect sent. Send it again.")
    return cast(JSON, payload)


@dataclass
class Item:
    """One connection, as it'll come over (or why it can't)."""

    key: str  # <client>:<connection id>, or every:<connection id>
    name: str
    spec: JSON
    transport: str  # "command" or "url"
    problem: str | None
    secrets: int = 0


@dataclass
class ClientPlan:
    name: str
    exists: bool
    projects: list[str]  # where its connections are linked: its own, or one made for it
    new_project: str | None
    items: list[Item] = field(default_factory=list[Item])


def _values(fields: Any) -> tuple[dict[str, str], int]:
    """A connection's settings and keys by name (env for a command, headers for a URL), and how
    many were keys. A key that was never saved is left out."""
    values: dict[str, str] = {}
    secrets = 0
    for name, given in cast(JSON, fields or {}).items():
        if not isinstance(given, dict):
            continue
        entry = cast(JSON, given)
        if isinstance(entry.get("value"), str):
            values[str(name)] = str(entry["value"])
        elif isinstance(entry.get("secret"), str):
            values[str(name)] = str(entry["secret"])
            secrets += 1
    return values, secrets


def item(key: str, raw: Any) -> Item:
    connection = cast(JSON, raw) if isinstance(raw, dict) else {}
    name = str(connection.get("name") or connection.get("id") or "tool")
    values, secrets = _values(connection.get("fields"))
    if connection.get("kind") == "remote":
        spec: JSON = {"url": str(connection.get("url") or ""), "headers": values}
        if connection.get("transport") == "sse":
            return Item(key, name, {}, "url", "uses SSE, not supported yet")
        problem = (
            "its sign-in was made in Ringkeeper Connect: sign in to it again here, from "
            "Connectors > Browse connectors"
            if connection.get("auth") == "oauth"
            else None
        )
        return Item(key, name, spec, "url", problem, secrets)
    command = str(connection.get("command") or "")
    args = [str(a) for a in cast(list[Any], connection.get("args") or [])]
    spec = {"command": command, "args": args, "env": values}
    problem = claude_desktop.missing_command(command) if command else "has no command to start"
    return Item(key, name, spec, "command", problem, secrets)


def done(session: Session) -> dict[str, str]:
    """What's been brought in already: its key -> the connection."""
    rows = session.scalars(select(Instance).where(Instance.type == InstanceType.MCP)).all()
    return {str(i.config.get("server")): i.name for i in rows if i.config.get("source") == SOURCE}


def _slug(text: str) -> str:
    slug = re.sub("-+", "-", "".join(c if c.isalnum() else "-" for c in text.lower())).strip("-")
    return (slug or "client")[:50]


def plan(session: Session, payload: JSON) -> tuple[list[ClientPlan], list[Item]]:
    """What bringing it in would do: per client, and the tools for every client."""
    planned: list[ClientPlan] = []
    for raw in cast(list[Any], payload.get("clients") or []):
        sent = cast(JSON, raw) if isinstance(raw, dict) else {}
        name = " ".join(str(sent.get("name") or "").split())
        if not name or sent.get("archived"):
            continue
        found = clients.find(session, name)
        slug = found.slug if found is not None else None
        owned = (
            sorted(session.scalars(select(Project.name).where(Project.owner == slug)))
            if slug
            else []
        )
        new_project = None if owned else _free_project(session, name)
        entry = ClientPlan(name, found is not None, owned or [new_project or ""], new_project)
        for c in cast(list[Any], sent.get("connections") or []):
            conn = cast(JSON, c) if isinstance(c, dict) else {}
            if conn.get("off"):
                continue
            entry.items.append(item(f"{_slug(name)}:{conn.get('id')}", conn))
        planned.append(entry)
    every = [
        item(f"every:{cast(JSON, c).get('id')}", c)
        for c in cast(list[Any], payload.get("everyClient") or [])
        if isinstance(c, dict) and not cast(JSON, c).get("off")
    ]
    return planned, every


def _free_project(session: Session, name: str) -> str:
    base, n = _slug(name), 2
    slug = base
    while projects.find_project(session, slug) is not None:
        slug, n = f"{base}-{n}", n + 1
    return slug


def summary(session: Session, payload: JSON) -> JSON:
    """What the page shows before the click: no keys."""
    planned, every = plan(session, payload)
    brought = done(session)

    def shown(it: Item) -> JSON:
        return {
            "name": it.name,
            "transport": it.transport,
            "secrets": it.secrets,
            "problem": it.problem,
            "brought_in_as": brought.get(it.key),
        }

    return {
        "sent_at": payload.get("createdAt"),
        "clients": [
            {
                "name": c.name,
                "exists": c.exists,
                "projects": c.projects,
                "new_project": c.new_project,
                "connections": [shown(i) for i in c.items],
            }
            for c in planned
        ],
        "every_client": [shown(i) for i in every],
    }


def _make(
    session: Session,
    settings: Settings,
    it: Item,
    *,
    owner: str,
    linked: list[str],
    shared: bool,
) -> str:
    name = claude_desktop.free_name(session, it.name)
    ref = secrets_in.store_connection(
        session, settings, name, InstanceType.MCP, json.dumps(it.spec)
    )
    config: JSON = {
        "source": SOURCE,
        "server": it.key,
        "transport": it.transport,
        "preview": claude_desktop.mcp.preview(it.spec),
    }
    instances.create_instance(
        session,
        InstanceIn(
            name=name, title=it.name, type=InstanceType.MCP,
            env=Env.DEV if shared else Env.PROD, secret_ref=ref, owner=owner, config=config,
            shared=shared, all_projects=shared,
        ),
    )  # fmt: skip
    for project in linked:
        bindings.set_binding(session, BindingIn(project=project, instance=name))
    return name


def bring_in(session: Session, settings: Settings, payload: JSON) -> JSON:
    """Make what the plan says. Returns what was made and what was left, with why."""
    planned, every = plan(session, payload)
    brought = done(session)
    made: list[str] = []
    left: list[str] = []
    new_clients: list[str] = []
    new_projects: list[str] = []
    for c in planned:
        usable = [i for i in c.items if i.problem is None and i.key not in brought]
        left += [f"{c.name}: {i.name} ({i.problem})" for i in c.items if i.problem]
        if not usable and c.exists:
            continue
        slug = clients.ensure(session, c.name)
        if not c.exists:
            new_clients.append(c.name)
        linked = c.projects
        if c.new_project:
            projects.create_project(
                session, ProjectIn.model_validate({"name": c.new_project, "owner": slug,
                                                   "title": c.name})
            )  # fmt: skip
            new_projects.append(c.new_project)
            linked = [c.new_project]
        for it in usable:
            made.append(_make(session, settings, it, owner=slug, linked=linked, shared=False))
    for it in every:
        if it.problem:
            left.append(f"for every client: {it.name} ({it.problem})")
        elif it.key not in brought:
            made.append(
                _make(session, settings, it, owner=instances.SHARED_OWNER, linked=[], shared=True)
            )
    if not made and not new_clients:
        raise RuleViolationError("There's nothing new to bring in: it's all here already.")
    admin_audit.record(
        session, "connect.brought_in",
        f"brought in from Ringkeeper Connect: {len(new_clients)} new clients, "
        f"{len(made)} connections",
        by="owner", details={"connections": made, "clients": new_clients},
    )  # fmt: skip
    return {"connections": made, "clients": new_clients, "projects": new_projects, "left": left}
