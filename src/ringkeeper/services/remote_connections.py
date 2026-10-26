"""Connections to remote MCP services (ringkeeper.remote): signed in, or with a pasted token.

Either way the result is an `mcp` connection: its secret (in the vault) names
the server's URL with the OAuth tokens or the token's header, and its config
says which service it is, so the gateway knows how a project's link to it can
be narrowed. A connection belongs to one client; with `project`, it's linked
to that project too.
"""

import json
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper import remote
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import admin_audit, bindings, instances, projects, secrets_in


@dataclass(frozen=True)
class NewConnection:
    remote: str
    name: str
    owner: str
    env: Env
    project: str | None = None
    scope: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "remote": self.remote, "name": self.name, "owner": self.owner,
            "env": self.env.value, "project": self.project, "scope": self.scope or {},
        }  # fmt: skip

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewConnection":
        return cls(
            remote=str(data["remote"]), name=str(data["name"]), owner=str(data["owner"]),
            env=Env(data["env"]), project=data.get("project"), scope=data.get("scope") or None,
        )  # fmt: skip


def service(key: str) -> remote.Remote:
    found = remote.BY_KEY.get(key)
    if found is None:
        raise RuleViolationError(f"'{key}' isn't a service Ringkeeper connects to.")
    return found


def check(session: Session, new: NewConnection) -> None:
    """Before anything is stored or anyone is sent to sign in."""
    service(new.remote)
    if instances.find_instance(session, new.name) is not None:
        raise RuleViolationError(f"A connection named '{new.name}' already exists.")
    if new.project is not None:
        owner = projects.get_project(session, new.project).owner
        if not bindings.same_client(owner, new.owner):
            raise RuleViolationError(
                f"'{new.project}' is {owner}'s project; this connection is for {new.owner}."
            )


def token_secret(key: str, token: str) -> str:
    found = service(key)
    if found.paste is None:
        raise RuleViolationError(f"{found.name} connects by signing in only.")
    token = "".join(token.split())
    if not token:
        raise RuleViolationError("The token is empty.")
    header = f"{found.auth_scheme} {token}"
    return json.dumps({"url": found.url, "headers": {"Authorization": header}})


def create(
    session: Session, settings: Settings, new: NewConnection, secret: str, *, how: str
) -> Instance:
    """Store the secret, add the connection (and its link). `how`: signin or token."""
    check(session, new)
    found = service(new.remote)
    ref = secrets_in.store_connection(session, settings, new.name, InstanceType.MCP, secret)
    config = {"remote": found.key, "source": how, "preview": found.url}
    instance = instances.create_instance(
        session,
        InstanceIn(
            name=new.name, type=InstanceType.MCP, env=new.env, secret_ref=ref, owner=new.owner,
            config=config,
        ),
    )  # fmt: skip
    via = "signed in" if how == "signin" else "token"
    admin_audit.record(
        session, "connection.create",
        f"added {found.name} connection {new.name} ({new.env}, {via})",
        by="owner", instance=new.name,
    )  # fmt: skip
    if new.project is not None:
        bindings.check_same_client(session, new.project, new.name)
        bindings.set_binding(
            session,
            BindingIn(project=new.project, instance=new.name, scope=new.scope or {}),
        )
        admin_audit.record(
            session, "binding.set", f"connected {new.name}", by="owner", project=new.project,
            instance=new.name,
        )  # fmt: skip
    return instances.get_instance(session, instance.name)


def catalog() -> list[dict[str, Any]]:
    """What the web app shows: each service, whether it signs in, and how it narrows."""
    return [
        {
            "key": r.key, "name": r.name, "signin": r.signin, "paste": r.paste,
            "paste_url": r.paste_url, "paste_steps": r.paste_steps, "note": r.note,
            "scope": [{"key": f.key, "label": f.label, "hint": f.hint} for f in r.scope],
        }
        for r in remote.CATALOG
    ]  # fmt: skip
