"""An MCP server connection's tool rules: the server's hints, and the user's choices.

The hints (MCP annotations) are remembered each time Ringkeeper lists the
server's tools, so the gateway can decide a call without starting the server
first. A change in them changes what runs without asking, so it is audited.
"""

from sqlalchemy.orm import Session

from ringkeeper.adapters import mcp
from ringkeeper.adapters.base import JSON
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType
from ringkeeper.services import admin_audit, instances

LABELS = {mcp.READ: "runs without asking", mcp.APPROVE: "waits", mcp.CONFIRM: "needs the name"}


def remember(name: str, listed: JSON) -> None:
    """Keep the hints (and argument names) from a list_tools answer with the connection."""
    hints, arguments = mcp.annotations_of(listed), mcp.arguments_of(listed)
    with session_scope() as session:
        instance = instances.find_instance(session, name)
        if instance is None or instance.type is not InstanceType.MCP:
            return
        config = dict(instance.config or {})
        if config.get("annotations") == hints and config.get("arguments") == arguments:
            return
        before = config.get("annotations")
        instance.config = {**config, "annotations": hints, "arguments": arguments}
        if before == hints:
            return
        if before is not None:  # the first listing isn't news
            admin_audit.record(
                session, "connection.tool_hints", f"{name}: the server changed its tool hints",
                by="gateway", instance=name,
            )  # fmt: skip


def set_rules(session: Session, name: str, chosen: dict[str, str | None], *, by: str) -> JSON:
    """The user's choices per tool; None (or a missing tool) goes back to the default."""
    instance = instances.get_instance(session, name)
    if instance.type is not InstanceType.MCP:
        raise RuleViolationError("Only MCP server connections have tool rules.")
    if bad := sorted(
        t for t, level in chosen.items() if level is not None and level not in mcp.LEVELS
    ):
        raise RuleViolationError(f"Unknown rule for {', '.join(bad)}.")
    rules = {t: level for t, level in sorted(chosen.items()) if level is not None}
    config = {k: v for k, v in (instance.config or {}).items() if k != "read_only_tools"}
    instance.config = {**config, "tool_rules": rules}
    summary = "; ".join(f"{t} {LABELS[level]}" for t, level in rules.items()) or "all defaults"
    admin_audit.record(
        session, "connection.tool_rules", f"{name}: {summary}", by=by, instance=name,
    )  # fmt: skip
    return rules
