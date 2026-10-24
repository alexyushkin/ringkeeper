"""The call pipeline: every gateway tool call and every approval goes through here.

Checks run in a fixed order, and the first failure refuses the call:

1. a valid worker token (verified before this module is reached; a missing
   caller is refused here too, defensively)
2. the instance exists (omitted only for reads, via the project's default)
3. the instance is bound to the token's project
4. the tool is in the binding's allowed_tools (empty = all)

Then reads run; writes to dev/staging run and are audited; writes to prod are
held as a decision unless the tool is in preapproved_writes.

Agents see one message for "no such instance" and "not bound", so a project
can't discover another client's instance names; the audit row keeps the
precise reason.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import partial
from typing import Any

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.orm import Session

from interlock.adapters import ADAPTERS
from interlock.adapters.base import JSON, Adapter, Target, ToolError, ToolKind, ToolSpec
from interlock.calls import Caller, ToolCall
from interlock.db import session_scope
from interlock.errors import InterlockError
from interlock.models import (
    AuditOutcome,
    Decision,
    Env,
    Instance,
    InstanceType,
    Project,
    ProjectInstance,
)
from interlock.scrub import scrub, scrub_obj
from interlock.services import audit, decisions
from interlock.vault import Vault, VaultError

logger = logging.getLogger(__name__)


class RefusalError(InterlockError):
    """A policy refusal. `str()` is for the agent; `reason` is for the audit log."""

    def __init__(self, message: str, reason: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason or message


@dataclass(frozen=True)
class Authorized:
    instance: str
    env: Env
    base_url: str | None
    secret_ref: str
    preapproved: bool


async def _db[T](fn: Callable[[Session], T]) -> T:
    """Run a unit of DB work in a worker thread (SQLAlchemy here is synchronous)."""

    def work() -> T:
        with session_scope() as session:
            return fn(session)

    return await anyio.to_thread.run_sync(work)


def _bound_names(project: Project, call: ToolCall) -> str:
    names = sorted(b.instance.name for b in project.bindings if b.instance_type == call.service)
    return ", ".join(names) or "none"


def authorize(session: Session, call: ToolCall, spec: ToolSpec) -> Authorized:
    """Checks 2-4. Raises RefusalError on the first one that fails."""
    caller = call.caller
    if caller.project is None:
        raise RefusalError("a valid worker token is required", "no verified caller")
    project = session.scalars(select(Project).where(Project.name == caller.project)).one_or_none()
    if project is None:
        raise RefusalError(
            f"project '{caller.project}' from the worker token no longer exists",
            "token project does not exist",
        )

    name = call.instance
    if name is None:
        if spec.kind is ToolKind.WRITE:
            raise RefusalError(
                f"'{call.tool}' writes, so it needs an explicit instance (instance=...). "
                f"{call.service} instances for {project.name}: {_bound_names(project, call)}",
                "write without an explicit instance",
            )
        default = next(
            (b for b in project.bindings if b.is_default and b.instance_type == call.service), None
        )
        if default is None:
            raise RefusalError(
                f"project '{project.name}' has no default {call.service} instance; pass "
                f"instance=... ({_bound_names(project, call)})",
                "read without instance and no default",
            )
        name = default.instance.name

    unavailable = (
        f"instance '{name}' is not available to project '{project.name}'. "
        f"{call.service} instances it can use: {_bound_names(project, call)}"
    )
    instance = session.scalars(select(Instance).where(Instance.name == name)).one_or_none()
    if instance is None:
        raise RefusalError(unavailable, f"instance '{name}' does not exist")
    binding = session.scalars(
        select(ProjectInstance).where(
            ProjectInstance.project_id == project.id, ProjectInstance.instance_id == instance.id
        )
    ).one_or_none()
    if binding is None:
        raise RefusalError(
            unavailable, f"instance '{name}' is not bound to project '{project.name}'"
        )
    if instance.type != call.service:
        raise RefusalError(
            f"instance '{name}' is a {instance.type} instance; "
            f"call it through /mcp/{instance.type}",
            f"instance is {instance.type}, endpoint is {call.service}",
        )

    if binding.allowed_tools and call.tool not in binding.allowed_tools:
        raise RefusalError(
            f"tool '{call.tool}' is not allowed on instance '{name}' for project "
            f"'{project.name}'. Allowed: {', '.join(binding.allowed_tools)}",
            "tool not in allowed_tools",
        )
    return Authorized(
        instance=instance.name,
        env=instance.env,
        base_url=instance.base_url,
        secret_ref=instance.secret_ref,
        preapproved=call.tool in binding.preapproved_writes,
    )


def _record(call: ToolCall, outcome: AuditOutcome, **fields: Any) -> None:
    with session_scope() as session:
        audit.record(session, call, outcome, **fields)


def _authorize_or_refuse(call: ToolCall, spec: ToolSpec) -> Authorized:
    with session_scope() as session:
        try:
            return authorize(session, call, spec)
        except RefusalError as refusal:
            caught = refusal
    _record(call, AuditOutcome.REFUSED, reason=caught.reason)
    raise caught


def _target(vault: Vault, adapter: Adapter, auth: Authorized) -> Target:
    try:
        secret = vault.resolve(auth.secret_ref)
    except VaultError as error:
        raise ToolError(
            f"the secret for instance '{auth.instance}' is unavailable: {error}"
        ) from None
    secret.also_scrub(*adapter.secret_fragments(secret.reveal()))
    return Target(auth.instance, auth.env, auth.base_url, secret)


async def _invoke(fn: Callable[..., Any], target: Target, args: dict[str, Any], tool: str) -> JSON:
    try:
        return await fn(target, **args)
    except ToolError as error:
        raise ToolError(scrub(str(error))) from None
    except Exception:
        logger.exception("tool %s failed unexpectedly", tool)  # records are scrubbed
        raise ToolError(f"'{tool}' failed unexpectedly; see the gateway log") from None


def unauthenticated(service: InstanceType, reason: str) -> None:
    """Audit a request refused at the token check (no verified caller to name)."""
    _record(
        ToolCall(Caller(None, None, None), service, "", None), AuditOutcome.REFUSED, reason=reason
    )


async def run_call(call: ToolCall, vault: Vault) -> JSON:
    """Run one gateway tool call through every check. Returns a scrubbed result."""
    adapter = ADAPTERS[call.service]
    spec = adapter.tool(call.tool)
    auth = await anyio.to_thread.run_sync(_authorize_or_refuse, call, spec)
    call = replace(call, instance=auth.instance)
    is_write = spec.kind is ToolKind.WRITE

    try:
        target = _target(vault, adapter, auth)
    except ToolError as error:
        if is_write:
            await anyio.to_thread.run_sync(
                partial(_record, call, AuditOutcome.FAILED, reason=str(error))
            )
        raise

    if scrub_obj(call.args) != call.args:
        # Held decisions keep args verbatim, so a secret must never get in there.
        reason = "arguments contain a secret value"
        await anyio.to_thread.run_sync(partial(_record, call, AuditOutcome.REFUSED, reason=reason))
        raise RefusalError(
            "the arguments contain a secret value from the vault; refusing the call", reason
        )

    if not is_write:
        return scrub_obj(await _invoke(spec.handler, target, call.args, call.tool))

    if auth.env is Env.PROD and not auth.preapproved:
        return await _hold(call, spec, target)

    try:
        result = await _invoke(spec.handler, target, call.args, call.tool)
    except ToolError as error:
        await anyio.to_thread.run_sync(
            partial(_record, call, AuditOutcome.FAILED, reason=str(error))
        )
        raise
    await anyio.to_thread.run_sync(partial(_record, call, AuditOutcome.EXECUTED, result=result))
    return scrub_obj(result)


async def _hold(call: ToolCall, spec: ToolSpec, target: Target) -> JSON:
    precondition: JSON | None = None
    if spec.precondition is not None:
        try:
            precondition = await _invoke(spec.precondition, target, call.args, call.tool)
        except ToolError as error:
            await anyio.to_thread.run_sync(
                partial(_record, call, AuditOutcome.FAILED, reason=f"precondition: {error}")
            )
            raise

    def hold(session: Session) -> tuple[str, str]:
        decision = decisions.hold(session, call, target.name, precondition=precondition)
        audit.record(session, call, AuditOutcome.HELD, decision_id=decision.id)
        return str(decision.id), decision.expires_at.isoformat()

    decision_id, expires_at = await _db(hold)
    return {
        "status": "held_for_approval",
        "decision_id": decision_id,
        "expires_at": expires_at,
        "message": (
            f"Held for approval: '{call.tool}' on prod instance '{target.name}' needs a human. "
            f"Decision {decision_id} (expires {expires_at}). Approve with "
            f"`interlock decision approve {decision_id}`."
        ),
    }


# --- Approval ---


def _finish(decision_id: str, call: ToolCall, outcome: AuditOutcome, **fields: Any) -> Decision:
    error = fields.get("reason") if outcome is not AuditOutcome.EXECUTED else None
    with session_scope() as session:
        decision = decisions.get_decision(session, decision_id, lock=True)
        decisions.finish(session, decision, result=fields.get("result"), error=error)
        audit.record(session, call, outcome, decision_id=decision.id, **fields)
        return decision


async def approve(ident: str, *, by: str | None = None, vault: Vault) -> Decision:
    """Claim a pending decision, re-check it against today's registry, then run it."""

    def claim(session: Session) -> tuple[str, ToolCall, JSON | None]:
        decisions.expire_stale(session)
        session.commit()
        decision = decisions.claim_for_approval(session, ident, by)
        caller = Caller(decision.project, decision.session_id, decision.token_id)
        call = ToolCall(caller, decision.service, decision.tool, decision.instance, decision.args)
        return str(decision.id), call, decision.precondition

    decision_id, call, stored_precondition = await _db(claim)
    finish = partial(_finish, decision_id, call)
    spec = ADAPTERS[call.service].tool(call.tool)

    def recheck(session: Session) -> Authorized | RefusalError:
        try:
            return authorize(session, call, spec)
        except RefusalError as refusal:
            return refusal

    auth = await _db(recheck)
    if isinstance(auth, RefusalError):
        reason = f"no longer allowed: {auth.reason}"
        return await anyio.to_thread.run_sync(partial(finish, AuditOutcome.REFUSED, reason=reason))

    try:
        target = _target(vault, ADAPTERS[call.service], auth)
        if spec.precondition is not None and stored_precondition is not None:
            current = await _invoke(spec.precondition, target, call.args, call.tool)
            if current != stored_precondition:
                reason = (
                    f"stale: the target changed since the write was held ({stored_precondition} "
                    f"-> {current}); ask the agent to redo the change"
                )
                return await anyio.to_thread.run_sync(
                    partial(finish, AuditOutcome.REFUSED, reason=reason)
                )
        result = await _invoke(spec.handler, target, call.args, call.tool)
    except ToolError as error:
        return await anyio.to_thread.run_sync(
            partial(finish, AuditOutcome.FAILED, reason=str(error))
        )
    return await anyio.to_thread.run_sync(partial(finish, AuditOutcome.EXECUTED, result=result))


def deny(ident: str, *, by: str | None = None, reason: str | None = None) -> Decision:
    with session_scope() as session:
        decisions.expire_stale(session)
        session.commit()
        return decisions.deny(session, ident, by=by, reason=reason)
