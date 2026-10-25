"""The schema as of migration 0008, for SQLite. Frozen: never edit it.

Migrations 0001-0008 were written for Postgres (native enums, arrays,
plpgsql), and existing server installs have already run them, so they stay
as they are. On SQLite they do nothing until 0008, which creates this whole
schema in one go. Every migration after 0008 runs on both databases.

What's different from Postgres:
- enums are VARCHAR without a CHECK (the Python enums and the API validate);
- arrays and JSONB are JSON text;
- the slug and secret_ref CHECKs use GLOB (SQLite has no regex): a looser
  backstop, since the API and registry validate both exactly;
- notifications go through rk_notify() (see storage.events), and UUIDs in
  them through rk_uuid(), so payloads match Postgres's.
"""

from typing import Any

import sqlalchemy as sa
from alembic import op

NOW = sa.text("(strftime('%Y-%m-%d %H:%M:%f000', 'now'))")  # SQLAlchemy's own text format
NEW_UUID = sa.text("(lower(hex(randomblob(16))))")  # sa.Uuid stores 32 hex digits here
EMPTY_LIST = sa.text("'[]'")
EMPTY_DOC = sa.text("'{}'")
FALSE = sa.text("0")

metadata = sa.MetaData()


def _slug(column: str) -> str:
    """Postgres: ^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"""
    return (
        f"length({column}) BETWEEN 1 AND 63 AND {column} NOT GLOB '*[^a-z0-9-]*' "
        f"AND {column} GLOB '[a-z0-9]*' AND {column} GLOB '*[a-z0-9]'"
    )


# Postgres: ^(file|aws-sm)://[^\s?#@]+$  (whitespace here: space, tab, CR, LF)
POINTER_CHECK = (
    "(secret_ref GLOB 'file://?*' OR secret_ref GLOB 'aws-sm://?*') "
    "AND secret_ref NOT GLOB '*[ ?#@' || char(9, 10, 13) || ']*'"
)


def _enum(*values: str) -> sa.Enum:
    return sa.Enum(*values, native_enum=False, create_constraint=False)


AUTONOMY = _enum("strict", "standard", "relaxed")
INSTANCE_TYPE = _enum("supabase", "n8n", "neon", "vercel", "airtable", "google")
INSTANCE_ENV = _enum("prod", "staging", "dev")
DECISION_STATUS = _enum(
    "pending", "approved", "denied", "executed", "failed", "expired", "answered"
)
DECISION_KIND = _enum("route", "approve", "plan", "question")
AUDIT_OUTCOME = _enum("refused", "held", "executed", "failed", "answered", "changed")
AUTH_MODE = _enum("api_key", "subscription")
TASK_STATUS = _enum(
    "queued", "running", "succeeded", "failed", "timed_out", "stopped", "budget_exceeded",
    "needs_input", "waiting",
)  # fmt: skip
TASK_KIND = _enum("feature", "fix", "data", "content", "ops")
TASK_RISK = _enum("read", "write", "prod-write")
COMPLEXITY = _enum("low", "medium", "high")
SESSION_ROLE = _enum("worker", "architect")
ARCHITECT_PHASE = _enum("planning", "awaiting_plan", "building", "reviewing", "done")
MESSAGE_ROLE = _enum("user", "router", "system")


def _id() -> sa.Column[Any]:
    return sa.Column("id", sa.Uuid(), server_default=NEW_UUID, primary_key=True)


def _serial() -> sa.Column[Any]:
    return sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True)


def _at(name: str, *, now: bool = False, nullable: bool | None = None) -> sa.Column[Any]:
    return sa.Column(
        name, sa.DateTime(timezone=True), server_default=NOW if now else None,
        nullable=(not now) if nullable is None else nullable,
    )  # fmt: skip


def _text(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.Text(), nullable=nullable)


def _fk(column: str, target: str, name: str, ondelete: str | None = None) -> sa.Column[Any]:
    return sa.Column(column, sa.Uuid(), sa.ForeignKey(target, name=name, ondelete=ondelete))


sa.Table(
    "projects", metadata,
    _id(),
    sa.Column("name", sa.String(63), nullable=False),
    _text("owner"),
    sa.Column("description", sa.Text(), server_default="", nullable=False),
    sa.Column("aliases", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    _text("repo_url", nullable=True),
    _text("workspace_path", nullable=True),
    sa.Column("autonomy", AUTONOMY, server_default="standard", nullable=False),
    sa.Column("checks", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    sa.Column("models", sa.JSON(), server_default=EMPTY_DOC, nullable=False),
    _at("created_at", now=True),
    _at("updated_at", now=True),
    sa.UniqueConstraint("name", name="projects_name_key"),
    sa.CheckConstraint(_slug("name"), name="ck_projects_name_slug"),
)  # fmt: skip

sa.Table(
    "instances", metadata,
    _id(),
    sa.Column("name", sa.String(63), nullable=False),
    sa.Column("type", INSTANCE_TYPE, nullable=False),
    sa.Column("env", INSTANCE_ENV, nullable=False),
    _text("base_url", nullable=True),
    _text("secret_ref"),
    _text("owner"),
    _at("created_at", now=True),
    _at("updated_at", now=True),
    _at("last_test_at", nullable=True),
    sa.Column("last_test_ok", sa.Boolean(), nullable=True),
    _text("last_test_message", nullable=True),
    sa.UniqueConstraint("name", name="instances_name_key"),
    sa.UniqueConstraint("id", "type", "env", name="uq_instances_id_type_env"),
    sa.CheckConstraint(_slug("name"), name="ck_instances_name_slug"),
    sa.CheckConstraint(POINTER_CHECK, name="ck_instances_secret_ref_pointer"),
)  # fmt: skip

sa.Table(
    "project_instances", metadata,
    sa.Column("project_id", sa.Uuid(), primary_key=True),
    sa.Column("instance_id", sa.Uuid(), primary_key=True),
    sa.Column("instance_type", INSTANCE_TYPE, nullable=False),
    sa.Column("instance_env", INSTANCE_ENV, nullable=False),
    sa.Column("is_default", sa.Boolean(), server_default=FALSE, nullable=False),
    sa.Column("allowed_tools", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    sa.Column("preapproved_writes", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    sa.ForeignKeyConstraint(
        ["project_id"], ["projects.id"], name="fk_project_instances_project", ondelete="CASCADE"
    ),
    sa.ForeignKeyConstraint(
        ["instance_id", "instance_type", "instance_env"],
        ["instances.id", "instances.type", "instances.env"],
        name="fk_project_instances_instance", onupdate="CASCADE", ondelete="RESTRICT",
    ),
    sa.CheckConstraint(
        "NOT (is_default AND instance_env = 'prod')", name="ck_project_instances_prod_not_default"
    ),
    sa.Index(
        "uq_project_instances_one_default_per_type", "project_id", "instance_type",
        unique=True, sqlite_where=sa.text("is_default"),
    ),
)  # fmt: skip

sa.Table(
    "threads", metadata,
    _id(),
    _text("title"),
    _text("source"),
    sa.Column("origin_chat_id", sa.BigInteger(), nullable=True),
    _at("created_at", now=True),
    _at("updated_at", now=True),
)  # fmt: skip

sa.Table(
    "messages", metadata,
    _id(),
    sa.Column(
        "thread_id", sa.Uuid(),
        sa.ForeignKey("threads.id", name="fk_messages_thread", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("role", MESSAGE_ROLE, nullable=False),
    _text("author"),
    _text("source"),
    _text("body"),
    sa.Column("input", sa.Text(), server_default=sa.text("'text'"), nullable=False),
    sa.Column("routing", sa.JSON(), nullable=True),
    _at("routed_at"),
    _text("route_error", nullable=True),
    _at("created_at", now=True),
    sa.CheckConstraint("input IN ('text', 'voice')", name="ck_messages_input"),
    sa.Index("ix_messages_thread_created_at", "thread_id", "created_at"),
    sa.Index(
        "ix_messages_unrouted", "created_at",
        sqlite_where=sa.text("role = 'user' AND routed_at IS NULL"),
    ),
)  # fmt: skip

sa.Table(
    "tasks", metadata,
    _id(),
    _text("project"),
    _text("prompt"),
    sa.Column("status", TASK_STATUS, server_default="queued", nullable=False),
    sa.Column("auth_mode", AUTH_MODE, nullable=False),
    sa.Column("max_turns", sa.Integer(), nullable=False),
    sa.Column("max_runtime_s", sa.Integer(), nullable=False),
    sa.Column("budget_usd", sa.Numeric(10, 4, asdecimal=False), nullable=True),
    _text("git_branch", nullable=True),
    _text("git_commit", nullable=True),
    sa.Column("changed_files", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    _text("error", nullable=True),
    sa.Column("stop_requested", sa.Boolean(), server_default=FALSE, nullable=False),
    _at("created_at", now=True),
    _at("started_at"),
    _at("finished_at"),
    _fk("thread_id", "threads.id", "fk_tasks_thread", "SET NULL"),
    _fk("message_id", "messages.id", "fk_tasks_message", "SET NULL"),
    sa.Column("kind", TASK_KIND, nullable=True),
    sa.Column("risk", TASK_RISK, nullable=True),
    sa.Column("complexity", COMPLEXITY, nullable=True),
    _text("outcome", nullable=True),
    _text("summary", nullable=True),
    sa.Column("phase", ARCHITECT_PHASE, nullable=True),
    sa.Column("review_round", sa.Integer(), server_default=sa.text("0"), nullable=False),
    sa.Column("failed_check_rounds", sa.Integer(), server_default=sa.text("0"), nullable=False),
    _text("worker_prompt", nullable=True),
    sa.CheckConstraint("max_turns > 0", name="ck_tasks_max_turns_positive"),
    sa.CheckConstraint("max_runtime_s > 0", name="ck_tasks_max_runtime_positive"),
    sa.CheckConstraint(
        "budget_usd IS NULL OR (budget_usd > 0 AND auth_mode = 'api_key')",
        name="ck_tasks_budget_needs_api_key",
    ),
    sa.CheckConstraint(
        "review_round >= 0 AND review_round <= 3", name="ck_tasks_review_round_range"
    ),
    sa.Index("ix_tasks_status_created_at", "status", "created_at"),
)  # fmt: skip

sa.Table(
    "sessions", metadata,
    _id(),
    sa.Column(
        "task_id", sa.Uuid(),
        sa.ForeignKey("tasks.id", name="fk_sessions_task", ondelete="CASCADE"), nullable=False,
    ),
    sa.Column("ordinal", sa.Integer(), nullable=False),
    _text("prompt"),
    sa.Column("status", TASK_STATUS, server_default="queued", nullable=False),
    _text("sdk_session_id", nullable=True),
    _text("container_id", nullable=True),
    _text("state_volume"),
    _text("token_jti", nullable=True),
    sa.Column("exit_code", sa.Integer(), nullable=True),
    sa.Column("turns", sa.Integer(), nullable=True),
    sa.Column("cost_usd", sa.Numeric(12, 6, asdecimal=False), nullable=True),
    sa.Column("usage", sa.JSON(), nullable=True),
    sa.Column("model_usage", sa.JSON(), nullable=True),
    _text("result_subtype", nullable=True),
    _text("error", nullable=True),
    _at("created_at", now=True),
    _at("started_at"),
    _at("finished_at"),
    sa.Column("role", SESSION_ROLE, server_default="worker", nullable=False),
    _text("model", nullable=True),
    sa.Column("action", sa.JSON(), nullable=True),
    sa.Column("checks", sa.JSON(), nullable=True),
    sa.UniqueConstraint("task_id", "ordinal", name="uq_sessions_task_ordinal"),
    sa.Index("ix_sessions_status", "status"),
)  # fmt: skip

sa.Table(
    "session_events", metadata,
    _serial(),
    sa.Column(
        "session_id", sa.Uuid(),
        sa.ForeignKey("sessions.id", name="fk_session_events_session", ondelete="CASCADE"),
        nullable=False,
    ),
    sa.Column("seq", sa.Integer(), nullable=False),
    _at("at", now=True),
    _text("type"),
    sa.Column("payload", sa.JSON(), nullable=False),
    sa.UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),
    sa.Index("ix_session_events_at", "at"),
)  # fmt: skip

sa.Table(
    "token_revocations", metadata,
    sa.Column("jti", sa.Text(), primary_key=True),
    _at("revoked_at", now=True),
    _text("reason"),
    _text("revoked_by"),
    _at("expires_at"),
)  # fmt: skip

sa.Table(
    "decisions", metadata,
    _id(),
    sa.Column("status", DECISION_STATUS, server_default="pending", nullable=False),
    _text("project", nullable=True),
    _text("session_id", nullable=True),
    _text("token_id", nullable=True),
    _text("instance", nullable=True),
    sa.Column("service", INSTANCE_TYPE, nullable=True),
    _text("tool", nullable=True),
    sa.Column("args", sa.JSON(), nullable=True),
    _text("args_hash", nullable=True),
    sa.Column("precondition", sa.JSON(), nullable=True),
    _text("result", nullable=True),
    _text("error", nullable=True),
    _at("created_at", now=True),
    _at("expires_at", nullable=False),
    _at("decided_at"),
    _text("decided_by", nullable=True),
    _text("reason", nullable=True),
    _at("executed_at"),
    sa.Column("kind", DECISION_KIND, server_default="approve", nullable=False),
    sa.Column("payload", sa.JSON(), nullable=True),
    sa.Column("answer", sa.JSON(), nullable=True),
    _text("answered_via", nullable=True),
    _fk("task_id", "tasks.id", "fk_decisions_task", "SET NULL"),
    _fk("thread_id", "threads.id", "fk_decisions_thread", "SET NULL"),
    _fk("message_id", "messages.id", "fk_decisions_message", "SET NULL"),
    sa.CheckConstraint(
        "(status = 'pending') = (decided_at IS NULL)", name="ck_decisions_decided_iff_not_pending"
    ),
    sa.CheckConstraint("expires_at > created_at", name="ck_decisions_expiry_after_creation"),
    sa.CheckConstraint(
        "kind <> 'approve' OR (project IS NOT NULL AND session_id IS NOT NULL "
        "AND token_id IS NOT NULL AND instance IS NOT NULL AND service IS NOT NULL "
        "AND tool IS NOT NULL AND args IS NOT NULL AND args_hash IS NOT NULL)",
        name="ck_decisions_approve_has_call",
    ),
    sa.Index("ix_decisions_status_created_at", "status", "created_at"),
)  # fmt: skip

sa.Table(
    "audit_events", metadata,
    _serial(),
    _at("at", now=True),
    _text("project", nullable=True),
    _text("session_id", nullable=True),
    _text("token_id", nullable=True),
    _text("instance", nullable=True),
    sa.Column("service", INSTANCE_TYPE, nullable=True),
    _text("tool", nullable=True),
    sa.Column("outcome", AUDIT_OUTCOME, nullable=False),
    _text("reason", nullable=True),
    sa.Column("args", sa.JSON(), nullable=True),
    _text("args_hash", nullable=True),
    _text("result", nullable=True),
    _fk("decision_id", "decisions.id", "fk_audit_events_decision"),
    sa.Index("ix_audit_events_project_at", "project", "at"),
    sa.Index("ix_audit_events_instance_at", "instance", "at"),
    sa.Index("ix_audit_events_outcome_at", "outcome", "at"),
)  # fmt: skip

sa.Table(
    "routing_examples", metadata,
    _id(),
    _text("normalized"),
    _text("text"),
    _text("project"),
    sa.Column("kind", TASK_KIND, nullable=True),
    _fk("decision_id", "decisions.id", "fk_routing_examples_decision", "SET NULL"),
    _text("created_by", nullable=True),
    _at("created_at", now=True),
    sa.UniqueConstraint("normalized", name="uq_routing_examples_normalized"),
)  # fmt: skip

sa.Table(
    "notifications", metadata,
    _serial(),
    _fk("decision_id", "decisions.id", "fk_notifications_decision", "CASCADE"),
    _text("channel"),
    sa.Column("chat_id", sa.BigInteger(), nullable=False),
    sa.Column("external_id", sa.BigInteger(), nullable=True),
    _text("status"),
    _text("error", nullable=True),
    _at("created_at", now=True),
    _fk("message_id", "messages.id", "fk_notifications_message", "CASCADE"),
    _fk("task_id", "tasks.id", "fk_notifications_task", "CASCADE"),
    sa.UniqueConstraint("decision_id", "channel", "chat_id", name="uq_notifications_target"),
    sa.UniqueConstraint("message_id", "channel", "chat_id", name="uq_notifications_message"),
    sa.UniqueConstraint("task_id", "channel", "chat_id", name="uq_notifications_task"),
    sa.CheckConstraint(
        "(decision_id IS NOT NULL) + (message_id IS NOT NULL) + (task_id IS NOT NULL) = 1",
        name="ck_notifications_one_subject",
    ),
)  # fmt: skip

sa.Table(
    "voice_confirmations", metadata,
    _id(),
    _text("kind"),
    sa.Column("chat_id", sa.BigInteger(), nullable=False),
    _text("author"),
    _fk("decision_id", "decisions.id", "fk_voice_confirmations_decision", "CASCADE"),
    _text("transcript", nullable=True),
    sa.Column("external_id", sa.BigInteger(), nullable=True),
    sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
    _at("expires_at", nullable=False),
    _at("resolved_at"),
    _at("created_at", now=True),
    sa.Column("user_id", sa.BigInteger(), nullable=True),
    sa.CheckConstraint("kind IN ('answer', 'request')", name="ck_voice_confirmations_kind"),
    sa.CheckConstraint(
        "(kind = 'answer') = (decision_id IS NOT NULL)",
        name="ck_voice_confirmations_answer_has_decision",
    ),
    sa.CheckConstraint(
        "status IN ('pending', 'confirmed', 'redone', 'expired', 'used')",
        name="ck_voice_confirmations_status",
    ),
    sa.CheckConstraint(
        "(status = 'pending') = (resolved_at IS NULL)",
        name="ck_voice_confirmations_resolved_iff_not_pending",
    ),
    sa.Index(
        "ix_voice_confirmations_pending", "expires_at", sqlite_where=sa.text("status = 'pending'")
    ),
)  # fmt: skip

sa.Table(
    "settings_kv", metadata,
    sa.Column("key", sa.Text(), primary_key=True),
    sa.Column("value", sa.JSON(), nullable=False),
)  # fmt: skip

sa.Table(
    "owner", metadata,
    sa.Column("id", sa.Integer(), server_default=sa.text("1"), primary_key=True),
    _text("password_hash"),
    _at("created_at", now=True),
    _at("password_changed_at", now=True),
    sa.CheckConstraint("id = 1", name="ck_owner_single_row"),
)  # fmt: skip

sa.Table(
    "owner_sessions", metadata,
    sa.Column("token_hash", sa.Text(), primary_key=True),
    _at("created_at", now=True),
    _at("last_seen_at", now=True),
    _at("expires_at", nullable=False),
    _text("method"),
    _text("user_agent", nullable=True),
    _at("confirmed_at"),
)  # fmt: skip

sa.Table(
    "passkeys", metadata,
    _id(),
    sa.Column("credential_id", sa.LargeBinary(), nullable=False),
    sa.Column("public_key", sa.LargeBinary(), nullable=False),
    sa.Column("sign_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
    _text("name"),
    sa.Column("transports", sa.JSON(), server_default=EMPTY_LIST, nullable=False),
    _at("created_at", now=True),
    _at("last_used_at"),
    sa.UniqueConstraint("credential_id", name="uq_passkeys_credential_id"),
)  # fmt: skip

sa.Table(
    "login_attempts", metadata,
    _serial(),
    _text("key"),
    sa.Column("ok", sa.Boolean(), nullable=False),
    _at("at", now=True),
    sa.Index("ix_login_attempts_key_at", "key", "at"),
)  # fmt: skip


TASK = (
    "'type', 'task', 'task_id', rk_uuid(NEW.id), 'status', NEW.status, "
    "'project', NEW.project, 'thread_id', rk_uuid(NEW.thread_id)"
)
DECISION = (
    "'type', 'approval', 'decision_id', rk_uuid(NEW.id), 'kind', NEW.kind, "
    "'status', NEW.status, 'thread_id', rk_uuid(NEW.thread_id), 'task_id', rk_uuid(NEW.task_id)"
)
SESSION_EVENT = (
    "'type', 'session_event', 'session_id', rk_uuid(NEW.session_id), 'seq', NEW.seq, "
    "'task_id', rk_uuid((SELECT task_id FROM sessions WHERE id = NEW.session_id))"
)
MESSAGE = "'type', '{type}', 'message_id', rk_uuid(NEW.id), 'thread_id', rk_uuid(NEW.thread_id)"


def trigger_sql(name: str, event: str, table: str, body: str, when: str | None = None) -> str:
    condition = f" WHEN {when}" if when else ""
    return f"CREATE TRIGGER {name} {event} ON {table}{condition} BEGIN {body}; END"


def _notify(payload: str) -> str:
    return f"SELECT rk_notify('ringkeeper_events', json_object({payload}))"


def _refuse(operation: str) -> str:
    return f"SELECT RAISE(ABORT, 'audit_events is append-only ({operation} rejected)')"


# The audit log is append-only; and live updates for the web app (ids only),
# from triggers so that every writer counts, as on Postgres. All fixed text.
TRIGGERS = {
    "audit_events_no_update": ("BEFORE UPDATE", "audit_events", _refuse("UPDATE"), None),
    "audit_events_no_delete": ("BEFORE DELETE", "audit_events", _refuse("DELETE"), None),
    "ringkeeper_notify_session_event": (
        "AFTER INSERT", "session_events", _notify(SESSION_EVENT), None
    ),
    "ringkeeper_notify_task_insert": ("AFTER INSERT", "tasks", _notify(TASK), None),
    "ringkeeper_notify_task_update": (
        "AFTER UPDATE", "tasks", _notify(TASK),
        "NEW.status IS NOT OLD.status OR NEW.phase IS NOT OLD.phase",
    ),
    "ringkeeper_notify_decision_insert": ("AFTER INSERT", "decisions", _notify(DECISION), None),
    "ringkeeper_notify_decision_update": (
        "AFTER UPDATE", "decisions", _notify(DECISION),
        "NEW.status IS NOT OLD.status OR NEW.answer IS NOT OLD.answer",
    ),
    "ringkeeper_notify_message_insert": (
        "AFTER INSERT", "messages", _notify(MESSAGE.format(type="message")), None
    ),
    "ringkeeper_notify_message_update": (
        "AFTER UPDATE", "messages", _notify(MESSAGE.format(type="message_routed")),
        "NEW.routed_at IS NOT OLD.routed_at",
    ),
}  # fmt: skip


def upgrade() -> None:
    bind = op.get_bind()
    metadata.create_all(bind)
    for name, (event, table, body, when) in TRIGGERS.items():
        op.execute(trigger_sql(name, event, table, body, when))


def downgrade() -> None:
    for name in TRIGGERS:
        op.execute(f"DROP TRIGGER IF EXISTS {name}")
    metadata.drop_all(op.get_bind())
