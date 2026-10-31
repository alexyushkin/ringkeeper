"""Connections to remote MCP servers: signed in, or with a pasted token or header.

A server is one of three things:

- a service with built-in rules (ringkeeper.remote: Neon, Supabase, ...),
  named by its key; its link to a project can be narrowed, and its SQL tools
  are checked like a database's;
- a server from the catalog (services.catalog), named by its registry name;
- any server added by its address.

A catalog server or an address that is one of the services with built-in
rules connects as that service. Everything else gets the generic rules: the
server's own tool annotations decide what runs without asking, and a tool
without them waits for approval (adapters.mcp).

Either way the result is an `mcp` connection: its secret (in the vault) names
the server's URL with the OAuth tokens or the headers, and its config says
which server it is. A connection belongs to one client, or (a shared tool: a
server that holds no client's data) to you, for any client's projects; with
`project`, it's linked to that project too.
"""

import json
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper import remote
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import admin_audit, bindings, catalog, instances, projects, secrets_in

HEADER_NAME_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")


@dataclass(frozen=True)
class NewConnection:
    name: str
    owner: str
    env: Env
    remote: str | None = None  # a service with built-in rules
    url: str | None = None  # any other server's address
    server: str | None = None  # its name in the MCP Registry, if it came from there
    project: str | None = None
    scope: dict[str, str] | None = None
    shared: bool = False  # a shared tool (an MCP server with no client's data)
    all_projects: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "remote": self.remote, "url": self.url, "server": self.server, "name": self.name,
            "owner": self.owner, "env": self.env.value, "project": self.project,
            "scope": self.scope or {}, "shared": self.shared, "all_projects": self.all_projects,
        }  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewConnection":
        return cls(
            remote=data.get("remote"), url=data.get("url"), server=data.get("server"),
            name=str(data["name"]), owner=str(data["owner"]), env=Env(data["env"]),
            project=data.get("project"), scope=data.get("scope") or None,
            shared=bool(data.get("shared")), all_projects=bool(data.get("all_projects")),
        )  # fmt: skip


def service(key: str) -> remote.Remote:
    found = remote.BY_KEY.get(key)
    if found is None:
        raise RuleViolationError(f"'{key}' isn't a service Ringkeeper connects to.")
    return found


def resolve(new: NewConnection) -> NewConnection:
    """The connection with its address checked, and a known service recognized by it."""
    if new.remote:
        return replace(new, url=service(new.remote).url)
    if not new.url:
        raise RuleViolationError("Give the MCP server's address (https://...).")
    url = catalog.check_remote_url(new.url)
    known = next((r for r in remote.CATALOG if catalog.same_url(url, r.url)), None)
    if known is not None:
        return replace(new, remote=known.key, url=known.url)
    if new.scope:
        raise RuleViolationError("Only services with built-in rules can be narrowed.")
    return replace(new, url=url)


def url_of(new: NewConnection) -> str:
    return resolve(new).url or ""


def check(session: Session, new: NewConnection) -> NewConnection:
    """Before anything is stored or anyone is sent to sign in."""
    new = resolve(new)
    check_new(session, new)
    return new


def check_new(session: Session, new: NewConnection) -> None:
    """The name is free, and the project (if any) is the same client's."""
    if instances.find_instance(session, new.name) is not None:
        raise RuleViolationError(f"A connection named '{new.name}' already exists.")
    if new.project is not None:
        owner = projects.get_project(session, new.project).owner
        if not new.shared and not bindings.same_client(owner, new.owner):
            raise RuleViolationError(
                f"'{new.project}' is {owner}'s project; this connection is for {new.owner}."
            )


def can_sign_in(new: NewConnection) -> None:
    """Services known not to let apps register themselves say so before trying."""
    if new.remote and not service(new.remote).signin:
        raise RuleViolationError(
            f"{service(new.remote).name} doesn't offer sign-in. Paste a token instead."
        )


def token_secret(new: NewConnection, token: str) -> str:
    """A pasted token, sent the way the service expects (Authorization: Bearer ...)."""
    new = resolve(new)
    scheme = "Bearer"
    if new.remote:
        found = service(new.remote)
        if found.paste is None:
            raise RuleViolationError(f"{found.name} connects by signing in only.")
        scheme = found.auth_scheme
    token = "".join(token.split())
    if not token:
        raise RuleViolationError("The token is empty.")
    return json.dumps({"url": new.url, "headers": {"Authorization": f"{scheme} {token}"}})


def headers_secret(new: NewConnection, headers: dict[str, str]) -> str:
    """Headers the server needs (an API key header, say), as the catalog or the user names them."""
    new = resolve(new)
    cleaned = {k.strip(): v.strip() for k, v in headers.items() if k.strip() and v.strip()}
    if not cleaned:
        raise RuleViolationError("Fill in the header the server needs.")
    if bad := sorted(k for k in cleaned if not set(k) <= HEADER_NAME_CHARS):
        raise RuleViolationError(f"'{bad[0]}' isn't a header name.")
    return json.dumps({"url": new.url, "headers": cleaned})


def open_secret(new: NewConnection) -> str:
    """A server that needs nothing: only its address."""
    return json.dumps({"url": resolve(new).url, "headers": {}})


def create(
    session: Session, settings: Settings, new: NewConnection, secret: str, *, how: str
) -> Instance:
    """Store the secret, add the connection (and its link). `how`: signin, token, headers or
    open (nothing needed)."""
    new = check(session, new)
    ref = secrets_in.store_connection(session, settings, new.name, InstanceType.MCP, secret)
    config: dict[str, Any] = {"source": how, "preview": new.url}
    if new.remote:
        config["remote"] = new.remote
        label = service(new.remote).name
    else:
        label = new.server or new.url or "MCP server"
    if new.server:
        config["registry"] = new.server
    instance = instances.create_instance(
        session,
        InstanceIn(
            name=new.name, type=InstanceType.MCP, env=new.env, secret_ref=ref, owner=new.owner,
            config=config, shared=new.shared, all_projects=new.all_projects,
        ),
    )  # fmt: skip
    via = {"signin": "signed in", "token": "token", "headers": "headers", "open": "no key"}.get(
        how, how
    )
    if new.shared:
        via += ", shared tool"
    admin_audit.record(
        session, "connection.create",
        f"added {label} connection {new.name} ({new.env}, {via})",
        by="owner", instance=new.name,
    )  # fmt: skip
    link(session, new)
    return instances.get_instance(session, instance.name)


def link(session: Session, new: NewConnection) -> None:
    if new.project is None:
        return
    bindings.check_same_client(session, new.project, new.name)
    bindings.set_binding(
        session, BindingIn(project=new.project, instance=new.name, scope=new.scope or {})
    )
    admin_audit.record(
        session, "binding.set", f"connected {new.name}", by="owner", project=new.project,
        instance=new.name,
    )  # fmt: skip


def catalog_shelf() -> list[dict[str, Any]]:
    """The Recommended shelf: services with built-in rules, how they sign in and narrow."""
    return [
        {
            "key": r.key, "name": r.name, "url": r.url, "signin": r.signin, "paste": r.paste,
            "paste_url": r.paste_url, "paste_steps": r.paste_steps, "note": r.note,
            "scope": [{"key": f.key, "label": f.label, "hint": f.hint} for f in r.scope],
            "sql": bool(r.sql_tools),
        }
        for r in remote.CATALOG
        if r.shelf
    ]  # fmt: skip
