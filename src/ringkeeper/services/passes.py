"""Passes: an AI app on this computer (Claude Desktop, Claude Code, Cursor) using one project.

A pass reaches the gateway the way a task does, and every call goes through the same checks:
the project's own links, the same-client rule (checked again on each call), scope, holds,
audit, scrubbing, plan limits and undo. It reaches nothing else: not the model, not the
architect, not another project.

Its token lives in the vault; the database keeps only its hash, so a copied database can't be
used to call anything. Revoking a pass stops it on its next call.
"""

import hashlib
import re
import secrets
import sys
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ringkeeper.calls import Caller
from ringkeeper.config import Settings
from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import Pass
from ringkeeper.services.projects import get_project
from ringkeeper.vault import Vault

PREFIX = "rkp_"  # tells a pass from a worker token at a glance
SESSION = "pass:"  # its calls' session id, in the audit log and on approvals
REF = "file://ringkeeper/passes/{name}"
NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def is_pass_token(token: str) -> bool:
    return token.startswith(PREFIX)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ref(name: str) -> str:
    return REF.format(name=name)


def session_id(row: Pass) -> str:
    return f"{SESSION}{row.name}"


def is_pass_session(value: str | None) -> bool:
    return bool(value) and str(value).startswith(SESSION)


def find(session: Session, name: str) -> Pass | None:
    return session.scalars(
        select(Pass).where(Pass.name == name).options(selectinload(Pass.project))
    ).one_or_none()


def get(session: Session, name: str) -> Pass:
    found = find(session, name)
    if found is None:
        raise NotFoundError(f"There's no pass named {name}. It may have been removed.")
    return found


def list_passes(session: Session) -> list[Pass]:
    query = select(Pass).options(selectinload(Pass.project)).order_by(Pass.name)
    return list(session.scalars(query))


def create(session: Session, vault: Vault, name: str, project: str) -> Pass:
    """A new pass for one project. Its token goes to the vault, never back to the caller."""
    if not NAME.match(name):
        raise RuleViolationError(
            "A pass's name uses lowercase letters, digits and dashes, like acme-claude-desktop."
        )
    if find(session, name) is not None:
        raise AlreadyExistsError(f"There's already a pass named {name}. Pick another name.")
    owner = get_project(session, project)
    token = PREFIX + secrets.token_urlsafe(32)
    row = Pass(name=name, project=owner, token_hash=_hash(token))
    session.add(row)
    session.flush()
    vault.store(ref(name), token)
    return row


def revoke(session: Session, name: str, *, by: str) -> Pass:
    row = get(session, name)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        row.revoked_by = by
        session.flush()
    return row


def remove(session: Session, vault: Vault, name: str) -> None:
    """Gone: revoked first if it wasn't, and its token taken out of the vault."""
    row = get(session, name)
    session.delete(row)
    session.flush()
    vault.delete(ref(name))


def server_name(name: str) -> str:
    """What the AI app lists it as: ringkeeper-acme-claude-desktop."""
    return f"ringkeeper-{name}"


def launch(settings: Settings, name: str) -> dict[str, Any]:
    """How an AI app on this computer starts the pass: this Python, Ringkeeper's bridge, and
    this install's data folder (and keychain name, when it isn't the usual one). No token:
    the bridge reads it from the vault."""
    env: dict[str, str] = {}
    if settings.keychain_service != Settings.model_fields["keychain_service"].default:
        env["RINGKEEPER_KEYCHAIN_SERVICE"] = settings.keychain_service
    args = ["-m", "ringkeeper", "mcp", "--pass", name, "--data-dir", str(settings.data_dir)]
    return {"command": sys.executable, "args": args, "env": env}


class PassError(Exception):
    """Why a pass token was refused, for the 401."""


def verify(session: Session, token: str) -> Caller:
    """The caller a pass token stands for. Raises PassError if it can't be used."""
    row = session.scalars(
        select(Pass).where(Pass.token_hash == _hash(token)).options(selectinload(Pass.project))
    ).one_or_none()
    if row is None:
        raise PassError("that pass doesn't exist (it may have been removed)")
    if row.revoked_at is not None:
        raise PassError(f"the pass {row.name} has been revoked")
    row.last_used_at = datetime.now(UTC)
    return Caller(row.project.name, session_id(row), session_id(row))
