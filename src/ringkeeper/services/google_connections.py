"""Google accounts as connections: many side by side, all through the owner's one
Google app, each with its own access (services.google_oauth).

An account is a client's (the same-client rule: only that client's projects)
or yours (a shared tool: any project, or all of them), the same choice as for
shared tools. A project may have several; one can be its default, and an
agent names the one it means (its connection name; list_instances shows each
one's address).

The sign-in finishes in the browser, outside the app, so each one started is
remembered by its `state` for the window to ask how it went.
"""

import re
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.adapters import google
from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, Instance, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import admin_audit, bindings, google_oauth, instances, projects, secrets_in

RULES = ("ask", "run")


@dataclass
class Outcome:
    state: str  # waiting, done, failed
    name: str | None = None
    error: str | None = None


_outcomes: dict[str, Outcome] = {}
_outcomes_lock = threading.Lock()


def started(state: str) -> None:
    with _outcomes_lock:
        _outcomes[state] = Outcome("waiting")


def outcome(state: str) -> Outcome | None:
    with _outcomes_lock:
        return _outcomes.get(state)


def _ended(state: str, result: Outcome) -> None:
    with _outcomes_lock:
        _outcomes[state] = result


def ended(state: str, error: str) -> None:
    """Google sent the browser back without access (cancelled, or an error)."""
    _ended(state, Outcome("failed", error=error))


def check_start(session: Session, connection: dict[str, Any]) -> None:
    """Before sending anyone to Google: the name (if chosen) is free, the project is the
    same client's (unless it's your own account), or the connection to change is Google."""
    if replace := connection.get("replace"):
        found = instances.get_instance(session, str(replace))
        if found.type is not InstanceType.GOOGLE:
            raise RuleViolationError(f"'{replace}' isn't a Google account.")
        return
    if (name := connection.get("name")) and instances.find_instance(session, str(name)):
        raise RuleViolationError(f"A connection named '{name}' already exists.")
    if project := connection.get("project"):
        owner = projects.get_project(session, str(project)).owner
        if not connection.get("shared") and not bindings.same_client(
            owner, str(connection["owner"])
        ):
            raise RuleViolationError(
                f"'{project}' is {owner}'s project; this account is for {connection['owner']}."
            )


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")[:50].strip("-")


def name_for(session: Session, email: str, owner: str, shared: bool) -> str:
    """seaside-info for info@seaside.example (a client's), or your own info-seaside."""
    local, _, domain = email.partition("@")
    site = domain.split(".")[0]
    base = _slug(f"{local}-{site}") if shared else _slug(f"{owner}-{local}")
    base = base or "google"
    name, n = base, 2
    while instances.find_instance(session, name) is not None:
        name, n = f"{base}-{n}", n + 1
    return name


def finish(
    session: Session, settings: Settings, found: google_oauth.Pending, state: str, code: str
) -> str:
    """The callback: store the account (or its new sign-in) and link it. Returns the name."""
    try:
        secret, email, granted = google_oauth.finish(settings, found, code)
        name = _store(session, settings, found.connection, secret, email, granted)
    except (google_oauth.SignInError, RuleViolationError) as failed:
        _ended(state, Outcome("failed", error=str(failed)))
        raise
    _ended(state, Outcome("done", name=name))
    return name


def _store(
    session: Session,
    settings: Settings,
    connection: dict[str, Any],
    secret: str,
    email: str,
    granted: list[str],
) -> str:
    if replace := connection.get("replace"):
        instance = instances.get_instance(session, str(replace))
        before = str(instance.config.get("email") or "")
        if before and before != email:
            raise RuleViolationError(
                f"That was {email}, but '{instance.name}' is {before}. Sign in as {before}."
            )
        secrets_in.store_connection(session, settings, instance.name, InstanceType.GOOGLE, secret)
        instance.config = {**instance.config, "email": email, "access": granted}
        admin_audit.record(
            session, "connection.update",
            f"{instance.name}: signed in again as {email} ({', '.join(granted)})",
            by="owner", instance=instance.name,
        )  # fmt: skip
        return instance.name

    shared = bool(connection.get("shared"))
    owner = instances.SHARED_OWNER if shared else str(connection["owner"])
    name = str(connection.get("name") or "") or name_for(session, email, owner, shared)
    if instances.find_instance(session, name) is not None:
        raise RuleViolationError(f"A connection named '{name}' already exists.")
    ref = secrets_in.store_connection(session, settings, name, InstanceType.GOOGLE, secret)
    instances.create_instance(
        session,
        InstanceIn(
            # Every write waits for approval whatever the kind of data, so a Google
            # account is never "prod": that lets a project make one its default.
            name=name, type=InstanceType.GOOGLE, env=Env.DEV, secret_ref=ref, owner=owner,
            config={"email": email, "access": granted, "source": "signin"},
            shared=shared, all_projects=shared and bool(connection.get("all_projects")),
        ),
    )  # fmt: skip
    via = "your own account, shared tool" if shared else f"{owner}'s account"
    admin_audit.record(
        session, "connection.create", f"added Google account {email} as {name} ({via})",
        by="owner", instance=name,
    )  # fmt: skip
    if project := connection.get("project"):
        bindings.check_same_client(session, str(project), name)
        bindings.set_binding(
            session, BindingIn(project=str(project), instance=name, is_default=False)
        )
        admin_audit.record(
            session, "binding.set", f"connected {name}", by="owner", project=str(project),
            instance=name,
        )  # fmt: skip
    return name


def tools(instance: Instance) -> list[dict[str, Any]]:
    """Each tool: read or write, whether this account was given its access, and (writes)
    whether it asks first or runs."""
    rules: dict[str, str] = dict(instance.config.get("write_rules") or {})
    access = set(instance.config.get("access") or [])
    return [
        {
            "name": spec.name,
            "kind": spec.kind.value,
            "description": " ".join(spec.description.split()),
            "allowed": bool(set(google.NEEDS[spec.name]) & access),
            "rule": rules.get(spec.name, "ask") if spec.kind.value == "write" else None,
        }
        for spec in google.ADAPTER.tools
    ]


def set_write_rules(session: Session, name: str, rules: dict[str, str]) -> dict[str, str]:
    """Per tool: "ask" (the default) or "run" without asking. Writes only."""
    instance = instances.get_instance(session, name)
    if instance.type is not InstanceType.GOOGLE:
        raise RuleViolationError(f"'{name}' isn't a Google account.")
    bad = sorted(t for t, r in rules.items() if t not in google.WRITES or r not in RULES)
    if bad:
        raise RuleViolationError(
            f"'{bad[0]}' isn't a Google write tool, or its rule isn't ask or run."
        )
    kept = {t: r for t, r in rules.items() if r == "run"}
    before = dict(instance.config.get("write_rules") or {})
    instance.config = {**instance.config, "write_rules": kept}
    changed = sorted(set(before) ^ set(kept))
    if changed:
        said = [f"{t} {'runs without asking' if t in kept else 'asks first'}" for t in changed]
        admin_audit.record(
            session, "connection.update", f"{name}: {', '.join(said)}", by="owner", instance=name
        )
    return kept
