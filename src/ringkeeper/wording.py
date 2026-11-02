"""Plain words for what the user reads outside the app: statuses and who answered.

Telegram, Slack, Discord and Windows notifications show these instead of the
stored codes ("budget_exceeded", "slack:U0123 (alex)").
"""

import re

from ringkeeper.models import DecisionStatus, InstanceType, TaskStatus

TASK_STATUS = {
    TaskStatus.QUEUED: "waiting to start",
    TaskStatus.RUNNING: "working",
    TaskStatus.SUCCEEDED: "done",
    TaskStatus.FAILED: "failed",
    TaskStatus.TIMED_OUT: "stopped at its time limit",
    TaskStatus.STOPPED: "stopped",
    TaskStatus.BUDGET_EXCEEDED: "stopped at its spending cap",
    TaskStatus.NEEDS_INPUT: "waiting for your answer",
    TaskStatus.WAITING: "waiting for your approval",
}

DECISION_STATUS = {
    DecisionStatus.PENDING: "waiting",
    DecisionStatus.APPROVED: "approved",
    DecisionStatus.DENIED: "denied",
    DecisionStatus.EXECUTED: "approved and done",
    DecisionStatus.FAILED: "approved, but it failed",
    DecisionStatus.EXPIRED: "ran out of time, so nothing was changed",
    DecisionStatus.ANSWERED: "answered",
}

SERVICES = {
    InstanceType.SUPABASE: "database",
    InstanceType.NEON: "database",
    InstanceType.N8N: "n8n",
    InstanceType.VERCEL: "Vercel",
    InstanceType.AIRTABLE: "Airtable",
    InstanceType.GOOGLE: "Google",
    InstanceType.TWILIO: "Twilio",
    InstanceType.GMAIL: "Gmail",
    InstanceType.MCP: "add-on tool",
    InstanceType.GITHUB: "GitHub",
}

APPS = {"slack": "Slack", "discord": "Discord", "telegram": "Telegram", "web": "the app"}
_WHO = re.compile(r"^(?P<app>[a-z]+):[^ ]+(?: \((?P<name>.+)\))?$")


def task_status(status: TaskStatus | str) -> str:
    try:
        return TASK_STATUS[TaskStatus(status)]
    except ValueError:
        return "finished"


def decision_status(status: DecisionStatus | str) -> str:
    try:
        return DECISION_STATUS[DecisionStatus(status)]
    except ValueError:
        return "decided"


def service(kind: InstanceType | str) -> str:
    """A kind of connection as the app names it: "database", "Airtable"."""
    try:
        return SERVICES[InstanceType(kind)]
    except ValueError:
        return str(kind)


def person(who: str | None) -> str:
    """'slack:U0123 (alex)' -> 'alex (Slack)'; 'owner' -> 'you'."""
    if not who or who in ("owner", "unknown"):
        return "you"
    found = _WHO.match(who)
    if found is None:
        return who
    app = APPS.get(found.group("app"), found.group("app").capitalize())
    return f"{found.group('name')} ({app})" if found.group("name") else f"you ({app})"


# A held call, for people: "Run a database change on acme-db" (the agent sees the tool name).
ACTIONS: dict[tuple[InstanceType, str], str] = {
    (InstanceType.SUPABASE, "execute"): "A database change",
    (InstanceType.AIRTABLE, "create_records"): "Adding Airtable records",
    (InstanceType.AIRTABLE, "update_records"): "Changing Airtable records",
    (InstanceType.AIRTABLE, "delete_records"): "Deleting Airtable records",
    (InstanceType.N8N, "update_workflow"): "Replacing an n8n workflow",
    (InstanceType.N8N, "activate_workflow"): "Turning on an n8n workflow",
    (InstanceType.N8N, "deactivate_workflow"): "Turning off an n8n workflow",
    (InstanceType.N8N, "retry_execution"): "Running an n8n workflow again",
    (InstanceType.N8N, "stop_execution"): "Stopping an n8n workflow run",
    (InstanceType.VERCEL, "redeploy"): "A Vercel redeploy",
    (InstanceType.VERCEL, "promote"): "Putting a Vercel deployment live",
    (InstanceType.VERCEL, "rollback"): "A Vercel rollback",
    (InstanceType.TWILIO, "send_sms"): "Sending a text",
    (InstanceType.GMAIL, "send_email"): "Sending an email",
    (InstanceType.GMAIL, "create_draft"): "Saving an email draft",
}


def action(service: InstanceType | None, tool: str | None, args: object = None) -> str:
    """What a held call does, in a few words."""
    if service is None or tool is None:
        return "A change"
    if (service, tool) in ACTIONS:
        return ACTIONS[(service, tool)]
    if service is InstanceType.MCP and isinstance(args, dict):
        inner = str(args.get("tool") or "")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
        if inner:
            return f"Running {inner.replace('_', ' ')}"
    return tool.replace("_", " ").capitalize()
