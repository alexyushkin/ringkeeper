"""The call pipeline: every gateway tool call and every approval goes through here.

Checks run in a fixed order, and the first failure refuses the call:

1. a valid worker token (verified before this module is reached; a missing
   caller is refused here too, defensively)
2. the instance exists (omitted only for reads, via the project's default)
3. the instance is bound to the token's project
4. the tool is in the binding's allowed_tools (empty = all)
5. the call stays inside the binding's scope, if it has one: IDs it pins are
   filled in or checked, and account-wide tools are refused (ringkeeper.remote)

Then reads run; writes to dev/staging run and are audited; writes to prod are
held as a decision unless the tool is in preapproved_writes.

Agents see one message for "no such instance" and "not bound", so a project
can't discover another client's instance names; the audit row keeps the
precise reason.
"""

import inspect
import logging
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any, cast

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.orm import Session

from ringkeeper import remote
from ringkeeper.adapters import ADAPTERS, agent_name
from ringkeeper.adapters.base import JSON, Adapter, Hold, Target, ToolError, ToolKind, ToolSpec
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RingkeeperError
from ringkeeper.models import (
    AuditEvent,
    AuditOutcome,
    Decision,
    Env,
    Instance,
    InstanceType,
    Project,
    ProjectInstance,
)
from ringkeeper.scrub import scrub, scrub_obj
from ringkeeper.services import (
    audit,
    decisions,
    google_oauth,
    holds,
    mcp_oauth,
    mcp_tools,
    revocations,
)
from ringkeeper.vault import Vault, VaultError

logger = logging.getLogger(__name__)


class RefusalError(RingkeeperError):
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
    config: JSON
    scope: dict[str, str] = field(default_factory=dict[str, str])


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
                f"{agent_name(call.service)} instances for {project.name}: "
                f"{_bound_names(project, call)}",
                "write without an explicit instance",
            )
        default = next(
            (b for b in project.bindings if b.is_default and b.instance_type == call.service), None
        )
        if default is None:
            raise RefusalError(
                f"project '{project.name}' has no default {agent_name(call.service)} "
                f"instance; pass instance=... ({_bound_names(project, call)})",
                "read without instance and no default",
            )
        name = default.instance.name

    unavailable = (
        f"instance '{name}' is not available to project '{project.name}'. "
        f"{agent_name(call.service)} instances it can use: {_bound_names(project, call)}"
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
            f"instance '{name}' is a {agent_name(instance.type)} instance; "
            f"call it through /mcp/{agent_name(instance.type)}",
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
        config=dict(instance.config or {}),
        scope=dict(binding.scope or {}),
    )


def _takes(spec: ToolSpec) -> list[str]:
    return list(inspect.signature(spec.handler).parameters)[1:]


def narrowed(call: ToolCall, spec: ToolSpec, auth: Authorized) -> JSON:
    """The call's arguments inside the binding's scope (check 5). Raises RefusalError."""
    rules = remote.spec_for(call.service.value, auth.config)
    if not auth.scope or rules is None:
        return call.args
    try:
        if call.service is not InstanceType.MCP:
            return remote.narrow(rules, auth.scope, call.tool, call.args, _takes(spec))
        if call.tool != "call_tool":
            return call.args  # list_tools: filtered after it answers
        tool = str(call.args.get("tool", ""))
        arguments = cast(JSON, call.args.get("arguments") or {})
        takes = cast(JSON, auth.config.get("arguments") or {}).get(tool)
        inner = remote.narrow(rules, auth.scope, tool, arguments, takes)
        return {**call.args, "arguments": inner}
    except remote.ScopeError as error:
        raise RefusalError(str(error), f"outside the link's scope: {error}") from None


def _limit_listing(result: JSON, auth: Authorized) -> JSON:
    """A scoped link's tool list: only what it can call, and what it's limited to."""
    rules = remote.spec_for(InstanceType.MCP.value, auth.config)
    if not auth.scope or rules is None:
        return result
    listed = cast(list[JSON], result.get("tools") or [])
    kept = [
        t for t in listed
        if remote.usable(rules, auth.scope, str(t.get("name")),
                         list(cast(JSON, t.get("input_schema") or {}).get("properties") or {}))
    ]  # fmt: skip
    return {**result, "tools": kept, "limited_to": dict(auth.scope)}


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


def _target(vault: Vault, adapter: Adapter, auth: Authorized, project: str | None) -> Target:
    try:
        secret = google_oauth.connection_secret(
            vault, adapter.service, auth.instance, auth.secret_ref
        )
    except VaultError as error:
        raise ToolError(
            f"the secret for instance '{auth.instance}' is unavailable: {error}"
        ) from None
    except mcp_oauth.SignInError as error:
        raise ToolError(
            f"the sign-in for '{auth.instance}' needs renewing ({error}); the user can sign "
            "in again under Connections"
        ) from None
    secret.also_scrub(*adapter.secret_fragments(secret.reveal()))
    return Target(auth.instance, auth.env, auth.base_url, secret, auth.config, project)


async def _invoke(fn: Callable[..., Any], target: Target, args: dict[str, Any], tool: str) -> JSON:
    try:
        return await fn(target, **args)
    except ToolError as error:
        raise ToolError(scrub(str(error))) from None
    except Exception:
        logger.exception("tool %s failed unexpectedly", tool)  # records are scrubbed
        raise ToolError(f"'{tool}' failed unexpectedly; see the gateway log") from None


def runs_without_asking(config: JSON, tool: str) -> bool:
    """The owner let this write run without asking on this connection (a Google
    account's per-tool setting, `write_rules`). It's still audited."""
    rules = cast(dict[str, str], config.get("write_rules") or {})
    return rules.get(tool) == "run"


def unauthenticated(
    service: InstanceType | None, reason: str, caller: Caller | None = None
) -> None:
    """Audit a request refused at the token check (service None: the architect endpoint).

    `caller` is set only for an authentic but revoked token: then we know who it was.
    """
    who = caller or Caller(None, None, None)
    if service is not None:
        _record(ToolCall(who, service, "", None), AuditOutcome.REFUSED, reason=reason)
        return
    with session_scope() as session:
        session.add(
            AuditEvent(
                project=who.project,
                session_id=who.session_id,
                token_id=who.token_id,
                tool="architect",
                outcome=AuditOutcome.REFUSED,
                reason=reason,
            )
        )


def bound_instances(caller: Caller) -> list[dict[str, Any]]:
    """The calling project's own instances, for the list_instances tool. No secrets."""
    if caller.project is None:
        raise RefusalError("a valid worker token is required", "no verified caller")
    with session_scope() as session:
        project = session.scalars(
            select(Project).where(Project.name == caller.project)
        ).one_or_none()
        if project is None:
            raise RefusalError(
                f"project '{caller.project}' from the worker token no longer exists",
                "token project does not exist",
            )
        return [
            {
                "name": b.instance.name,
                "type": agent_name(b.instance.type),
                "env": b.instance.env.value,
                "is_default": b.is_default,
                "allowed_tools": list(b.allowed_tools) or "all",
            }
            # A Google account says which one it is ("send from the info@ mailbox").
            | ({"account": b.instance.config["email"]} if b.instance.config.get("email") else {})
            for b in sorted(project.bindings, key=lambda b: (b.instance.type, b.instance.name))
        ]


def token_revoked(jti: str) -> bool:
    with session_scope() as session:
        return revocations.is_revoked(session, jti)


async def run_call(call: ToolCall, vault: Vault) -> JSON:
    """Run one gateway tool call through every check. Returns a scrubbed result."""
    adapter = ADAPTERS[call.service]
    spec = adapter.tool(call.tool)
    auth = await anyio.to_thread.run_sync(_authorize_or_refuse, call, spec)
    call = replace(call, instance=auth.instance)
    try:
        call = replace(call, args=narrowed(call, spec, auth))
    except RefusalError as refusal:
        await anyio.to_thread.run_sync(
            partial(_record, call, AuditOutcome.REFUSED, reason=refusal.reason)
        )
        raise
    is_write = spec.kind is ToolKind.WRITE

    try:
        target = _target(vault, adapter, auth, call.caller.project)
    except ToolError as error:
        if is_write:
            await anyio.to_thread.run_sync(
                partial(_record, call, AuditOutcome.FAILED, reason=str(error))
            )
        raise

    if spec.classify is not None:
        is_write = spec.classify(target, call.args) is ToolKind.WRITE

    if scrub_obj(call.args) != call.args:
        # Held decisions keep args verbatim, so a secret must never get in there.
        reason = "arguments contain a secret value"
        await anyio.to_thread.run_sync(partial(_record, call, AuditOutcome.REFUSED, reason=reason))
        raise RefusalError(
            "the arguments contain a secret value from the vault; refusing the call", reason
        )

    if not is_write:
        result = await _invoke(spec.handler, target, call.args, call.tool)
        if call.service is InstanceType.MCP and call.tool == "list_tools":
            # The server's hints decide later calls without starting it first.
            await anyio.to_thread.run_sync(mcp_tools.remember, auth.instance, result)
            result = _limit_listing(result, auth)
        return scrub_obj(result)

    held = spec.hold is Hold.ALWAYS or (
        spec.hold is Hold.PROD and auth.env is Env.PROD and not auth.preapproved
    )
    if held and not runs_without_asking(auth.config, call.tool):
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

    def hold(session: Session) -> tuple[str, str, str]:
        decision = decisions.hold(session, call, target.name, precondition=precondition)
        audit.record(session, call, AuditOutcome.HELD, decision_id=decision.id)
        return str(decision.id), decision.expires_at.isoformat(), holds.label(decision)

    decision_id, expires_at, label = await _db(hold)
    return {
        "status": "held_for_approval",
        "decision_id": decision_id,
        "expires_at": expires_at,
        "message": HELD_MESSAGE.format(label=label),
    }


# What the agent reads. The user already sees the held call in Approvals, so the
# agent mustn't ask about it (one action would show up twice) or retry it.
HELD_MESSAGE = (
    "Held: {label} waits for the user's approval in Ringkeeper. They already see it, "
    "so don't ask them about it and don't call it again. Finish anything that doesn't "
    "depend on it, then end your turn with a one-line note of what's waiting. You'll be "
    "resumed with the result once they decide."
)


# --- Approval ---


def _finish(decision_id: str, call: ToolCall, outcome: AuditOutcome, **fields: Any) -> Decision:
    error = fields.get("reason") if outcome is not AuditOutcome.EXECUTED else None
    with session_scope() as session:
        decision = decisions.get_decision(session, decision_id, lock=True)
        decisions.finish(session, decision, result=fields.get("result"), error=error)
        audit.record(session, call, outcome, decision_id=decision.id, **fields)
        return decision


async def approve(
    ident: str, *, by: str | None = None, vault: Vault, confirm: str | None = None
) -> Decision:
    """Claim a pending decision, re-check it against today's registry, then run it."""

    def claim(session: Session) -> tuple[str, ToolCall, JSON | None]:
        decisions.expire_stale(session)
        session.commit()
        decision = decisions.claim_for_approval(session, ident, by, confirm=confirm)
        return str(decision.id), decisions.held_call(decision), decision.precondition

    decision_id, call, stored_precondition = await _db(claim)
    finish = partial(_finish, decision_id, call)
    spec = ADAPTERS[call.service].tool(call.tool)

    def recheck(session: Session) -> Authorized | RefusalError:
        try:
            return authorize(session, call, spec)
        except RefusalError as refusal:
            return refusal

    auth = await _db(recheck)
    if not isinstance(auth, RefusalError):
        try:  # the link's scope as it is now
            narrowed(call, spec, auth)
        except RefusalError as refusal:
            auth = refusal
    if isinstance(auth, RefusalError):
        reason = f"no longer allowed: {auth.reason}"
        return await anyio.to_thread.run_sync(partial(finish, AuditOutcome.REFUSED, reason=reason))

    try:
        target = _target(vault, ADAPTERS[call.service], auth, call.caller.project)
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
