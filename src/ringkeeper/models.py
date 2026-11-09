"""ORM models for the registries.

The services layer enforces every rule with a readable error. The constraints
here are a backstop so the database can't be put in a bad state by a race or by
code that skips the services.

`project_instances` carries a copy of the instance's `type` and `env`, kept in
sync by a composite foreign key with ON UPDATE CASCADE. That lets plain
constraints express "one default per type per project" and "prod is never a
default", including when an instance is later changed to prod.
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from ringkeeper.storage.types import BigID, Doc, TextList, UTCDateTime, UUIDValue

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
SECRET_REF_SCHEMES = ("file", "aws-sm")
# Backstop only; schemas.validate_secret_ref is the real check.
SECRET_REF_DB_PATTERN = r"^(file|aws-sm)://[^\s?#@]+$"  # noqa: S105 (a regex)


class Autonomy(enum.StrEnum):
    STRICT = "strict"
    STANDARD = "standard"
    RELAXED = "relaxed"


class InstanceType(enum.StrEnum):
    SUPABASE = "supabase"
    N8N = "n8n"
    NEON = "neon"
    VERCEL = "vercel"
    AIRTABLE = "airtable"
    GOOGLE = "google"  # a Google account signed in through the owner's Google app
    TWILIO = "twilio"
    GMAIL = "gmail"  # IMAP/SMTP with an app password (the simple option)
    MCP = "mcp"  # an MCP server imported from Claude Desktop, run behind the gateway
    GITHUB = "github"  # a token, on github.com or GitHub Enterprise Server
    SMTP = "smtp"  # sending email through any provider's SMTP server
    RESEND = "resend"  # sending email through Resend
    MAILBOX = "mailbox"  # any mailbox over IMAP and SMTP (presets: Gmail, Outlook, Zoho, Yahoo)
    HTTP = "http"  # any HTTP API: an address, a way to sign in, optionally an OpenAPI file
    S3 = "s3"  # file storage: AWS S3, Cloudflare R2, S3-compatible, Vercel Blob
    VENDOR = "vendor"  # a business system through its own API (adapters.vendors)
    ORACLE = "oracle"  # an Oracle Database, with its wallet if it's an Autonomous one


class Env(enum.StrEnum):
    PROD = "prod"
    STAGING = "staging"
    DEV = "dev"


def _enum_values(enum_cls: type[enum.StrEnum]) -> list[str]:
    return [member.value for member in enum_cls]


autonomy_enum = Enum(Autonomy, name="autonomy", values_callable=_enum_values)
instance_type_enum = Enum(InstanceType, name="instance_type", values_callable=_enum_values)
instance_env_enum = Enum(Env, name="instance_env", values_callable=_enum_values)


class DecisionStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"  # claimed by an approver; execution in progress or interrupted
    DENIED = "denied"
    EXECUTED = "executed"
    FAILED = "failed"
    EXPIRED = "expired"
    ANSWERED = "answered"  # route and question decisions


class DecisionKind(enum.StrEnum):
    ROUTE = "route"  # which project should a message go to
    APPROVE = "approve"  # a held prod write from the gateway
    PLAN = "plan"  # an architect's plan, before any code is written
    QUESTION = "question"  # a worker (or the architect loop) asking the user


class AuditOutcome(enum.StrEnum):
    REFUSED = "refused"
    HELD = "held"
    EXECUTED = "executed"
    FAILED = "failed"
    ANSWERED = "answered"  # a decision was answered: who and how are recorded
    CHANGED = "changed"  # the owner changed settings, a project, a connection or a secret


decision_status_enum = Enum(DecisionStatus, name="decision_status", values_callable=_enum_values)
decision_kind_enum = Enum(DecisionKind, name="decision_kind", values_callable=_enum_values)
audit_outcome_enum = Enum(AuditOutcome, name="audit_outcome", values_callable=_enum_values)


class AuthMode(enum.StrEnum):
    API_KEY = "api_key"
    # For the operator's own install only (Agent SDK terms): never the default.
    SUBSCRIPTION = "subscription"


class TaskStatus(enum.StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    STOPPED = "stopped"
    BUDGET_EXCEEDED = "budget_exceeded"
    NEEDS_INPUT = "needs_input"  # a worker asked the user something; answering resumes it
    WAITING = "waiting"  # blocked on a decision (an architect's plan) or another session


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.STOPPED,
        TaskStatus.BUDGET_EXCEEDED,
    }
)
# Not running and not queued: a task in one of these can be resumed.
RESUMABLE_STATUSES = TERMINAL_STATUSES | {TaskStatus.NEEDS_INPUT}


class TaskKind(enum.StrEnum):
    FEATURE = "feature"  # goes through the architect loop
    FIX = "fix"
    DATA = "data"
    CONTENT = "content"
    OPS = "ops"


class TaskRisk(enum.StrEnum):
    READ = "read"
    WRITE = "write"
    PROD_WRITE = "prod-write"


class Complexity(enum.StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class SessionRole(enum.StrEnum):
    WORKER = "worker"
    ARCHITECT = "architect"


class ArchitectPhase(enum.StrEnum):
    PLANNING = "planning"
    AWAITING_PLAN = "awaiting_plan"
    BUILDING = "building"
    REVIEWING = "reviewing"
    DONE = "done"


class MessageRole(enum.StrEnum):
    USER = "user"
    ROUTER = "router"
    SYSTEM = "system"


auth_mode_enum = Enum(AuthMode, name="auth_mode", values_callable=_enum_values)
task_status_enum = Enum(TaskStatus, name="task_status", values_callable=_enum_values)
task_kind_enum = Enum(TaskKind, name="task_kind", values_callable=_enum_values)
task_risk_enum = Enum(TaskRisk, name="task_risk", values_callable=_enum_values)
complexity_enum = Enum(Complexity, name="complexity", values_callable=_enum_values)
session_role_enum = Enum(SessionRole, name="session_role", values_callable=_enum_values)
architect_phase_enum = Enum(ArchitectPhase, name="architect_phase", values_callable=_enum_values)
message_role_enum = Enum(MessageRole, name="message_role", values_callable=_enum_values)


class Base(DeclarativeBase):
    type_annotation_map = {uuid.UUID: UUIDValue()}  # noqa: RUF012 (SQLAlchemy's own hook)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint(f"name ~ '{SLUG_PATTERN}'", name="ck_projects_name_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(63), unique=True)
    # As people read it (services.titles); `name` is for URLs, IDs and the AI.
    title: Mapped[str] = mapped_column(Text, server_default="")
    owner: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    aliases: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    repo_url: Mapped[str | None] = mapped_column(Text)
    workspace_path: Mapped[str | None] = mapped_column(Text)
    autonomy: Mapped[Autonomy] = mapped_column(
        autonomy_enum, server_default=Autonomy.STANDARD.value
    )
    # Commands run in the worker container after each worker turn (architect loop).
    checks: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    # Model overrides for this project: keys architect, worker, low, medium, high.
    models: Mapped[dict[str, str]] = mapped_column(Doc, server_default="{}")
    # Where agents' notes are kept (services.memory).
    memory: Mapped[dict[str, Any]] = mapped_column(Doc, server_default="{}")
    # Its routines don't run while paused (services.routines).
    paused: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    bindings: Mapped[list["ProjectInstance"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Client(Base):
    """A client: its slug (URLs, IDs, and the owner of its projects and connections)
    and its name as typed. See services.clients."""

    __tablename__ = "clients"

    slug: Mapped[str] = mapped_column(String(63), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    # New tasks stop once the month's AI cost reaches it (services.client_month).
    monthly_cap_usd: Mapped[float | None] = mapped_column(Numeric(10, 2, asdecimal=False))
    # Where a request goes that names the client and needs no particular project (its email,
    # Google, Slack). A project's slug; with one project, that one (services.clients).
    default_project: Mapped[str | None] = mapped_column(Text)


class Agent(TimestampMixin, Base):
    """A project's agent: instructions, which of the project's connections it may use
    (shared tools only when ticked), and its model, effort and autonomy (empty: the
    project's). See services.agents."""

    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_agents_project_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_agents_project")
    )
    name: Mapped[str] = mapped_column(String(63))
    instructions: Mapped[str] = mapped_column(Text, server_default="")
    connections: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    model: Mapped[str | None] = mapped_column(Text)
    effort: Mapped[str | None] = mapped_column(Text)
    autonomy: Mapped[str | None] = mapped_column(Text)
    memory: Mapped[str | None] = mapped_column(Text)  # none: as its project does

    project: Mapped["Project"] = relationship()


class MemoryNote(Base):
    """One thing an agent or person left for later runs (services.memory). With Notion this is
    a copy of a row there, and that row is the truth."""

    __tablename__ = "memory_notes"
    __table_args__ = (Index("ix_memory_notes_project", "project_id", "backend"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_memory_notes_project")
    )  # none: the owner's shared notes
    backend: Mapped[str] = mapped_column(Text)
    agent: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    source_task: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[str | None] = mapped_column(Text)
    external_seen: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Routine(TimestampMixin, Base):
    """Runs one agent with a prompt: on a schedule, an event, or by hand. New routines
    start paused (a Test run first). See services.routines."""

    __tablename__ = "routines"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_routines_project")
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agents.id", ondelete="CASCADE", name="fk_routines_agent")
    )
    name: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    trigger: Mapped[str] = mapped_column(Text)  # schedule, event, manual
    schedule: Mapped[dict[str, Any] | None] = mapped_column(Doc)  # ringkeeper.schedules
    event: Mapped[dict[str, Any] | None] = mapped_column(Doc)  # {"source": ..., ...}
    budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4, asdecimal=False))
    max_steps: Mapped[int] = mapped_column(Integer, server_default=text("40"))
    max_minutes: Mapped[int] = mapped_column(Integer, server_default=text("15"))
    max_runs_per_day: Mapped[int] = mapped_column(Integer, server_default=text("10"))
    notify: Mapped[str] = mapped_column(Text, server_default="app")  # app, channels, none
    catch_up: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    paused: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    hook_secret: Mapped[str | None] = mapped_column(Text)
    next_run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    state: Mapped[dict[str, Any]] = mapped_column(Doc, server_default="{}")  # event cursors

    project: Mapped["Project"] = relationship()
    agent: Mapped[Agent] = relationship()


class RoutineRun(Base):
    """One run of a routine: what started it and the task it made, or why it was
    skipped (the day's limit, paused). An event starts one run (`event_key`)."""

    __tablename__ = "routine_runs"
    __table_args__ = (
        UniqueConstraint("routine_id", "event_key", name="uq_routine_runs_event"),
        Index("ix_routine_runs_routine_created", "routine_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    routine_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("routines.id", ondelete="CASCADE", name="fk_routine_runs_routine")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL", name="fk_routine_runs_task")
    )
    trigger: Mapped[str] = mapped_column(Text)  # schedule, catch_up, event, manual, test
    event_key: Mapped[str | None] = mapped_column(Text)
    event: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    missed: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    due_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    skipped: Mapped[str | None] = mapped_column(Text)  # why it didn't start
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Shortcut(Base):
    """A request saved on a project, one click away on Home."""

    __tablename__ = "shortcuts"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_shortcuts_project")
    )
    label: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    project: Mapped["Project"] = relationship()


class Trust(Base):
    """One tool on one connection of one project, running without asking until
    `expires_at`. Never for a change that needs the connection's name typed."""

    __tablename__ = "trusts"
    __table_args__ = (Index("ix_trusts_lookup", "project", "instance", "tool"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project: Mapped[str] = mapped_column(Text)
    instance: Mapped[str] = mapped_column(Text)
    tool: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    created_by: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Instance(TimestampMixin, Base):
    __tablename__ = "instances"
    __table_args__ = (
        CheckConstraint(f"name ~ '{SLUG_PATTERN}'", name="ck_instances_name_slug"),
        CheckConstraint(
            f"secret_ref ~ '{SECRET_REF_DB_PATTERN}'", name="ck_instances_secret_ref_pointer"
        ),
        # Target of the composite FK from project_instances.
        UniqueConstraint("id", "type", "env", name="uq_instances_id_type_env"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(63), unique=True)
    # As people read it ("n8n · n8n.geninfos.com"); the AI and URLs use `name`.
    title: Mapped[str | None] = mapped_column(Text)
    type: Mapped[InstanceType] = mapped_column(instance_type_enum)
    env: Mapped[Env] = mapped_column(instance_env_enum)
    base_url: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(Text)
    # A shared tool: an MCP server that holds no client's data (a browser, files on
    # this computer), yours, linkable to any client's projects. False: a client's
    # connection, linked only to that client's projects.
    shared: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Every project gets it, including projects added later: every project there is for
    # a shared tool, every project of its client for a client's connection.
    all_projects: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # ...except these clients' projects (a client matched ignoring case and spaces; a
    # shared tool only) and these projects. Only with all_projects; they aren't linked.
    except_clients: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    except_projects: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    # Service-specific settings that aren't secret (an MCP server: how it runs, a
    # preview of its command, which of its tools are read-only).
    config: Mapped[dict[str, Any]] = mapped_column(Doc, server_default="{}")
    # The last "Test connection" (a read-only probe): when, whether it worked, and what it said.
    last_test_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_message: Mapped[str | None] = mapped_column(Text)

    bindings: Mapped[list["ProjectInstance"]] = relationship(
        back_populates="instance", passive_deletes="all", passive_updates=True
    )


class ProjectInstance(Base):
    __tablename__ = "project_instances"
    __table_args__ = (
        ForeignKeyConstraint(
            ["instance_id", "instance_type", "instance_env"],
            ["instances.id", "instances.type", "instances.env"],
            name="fk_project_instances_instance",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "NOT (is_default AND instance_env = 'prod')",
            name="ck_project_instances_prod_not_default",
        ),
        Index(
            "uq_project_instances_one_default_per_type",
            "project_id",
            "instance_type",
            unique=True,
            postgresql_where=text("is_default"),
            sqlite_where=text("is_default"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_project_instances_project"),
        primary_key=True,
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    instance_type: Mapped[InstanceType] = mapped_column(instance_type_enum)
    instance_env: Mapped[Env] = mapped_column(instance_env_enum)
    is_default: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    allowed_tools: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    preapproved_writes: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    # IDs this project's calls are pinned to, e.g. {"project": ..., "branch": ...}
    # (ringkeeper.remote.narrow). Empty: the whole connection.
    scope: Mapped[dict[str, str]] = mapped_column(Doc, server_default="{}")

    project: Mapped[Project] = relationship(back_populates="bindings")
    instance: Mapped[Instance] = relationship(back_populates="bindings")


class Decision(Base):
    """Everything waiting for a human: the inbox.

    route: which project(s) a message goes to. approve: a held prod write.
    plan: an architect's plan. question: a worker asking the user. Names are
    stored as text so the history survives later registry changes; approval
    of a held write re-checks them.
    """

    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_decisions_decided_iff_not_pending",
        ),
        CheckConstraint("expires_at > created_at", name="ck_decisions_expiry_after_creation"),
        CheckConstraint(
            "kind <> 'approve' OR (project IS NOT NULL AND session_id IS NOT NULL "
            "AND token_id IS NOT NULL AND instance IS NOT NULL AND service IS NOT NULL "
            "AND tool IS NOT NULL AND args IS NOT NULL AND args_hash IS NOT NULL)",
            name="ck_decisions_approve_has_call",
        ),
        Index("ix_decisions_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[DecisionKind] = mapped_column(
        decision_kind_enum, server_default=DecisionKind.APPROVE.value
    )
    status: Mapped[DecisionStatus] = mapped_column(
        decision_status_enum, server_default=DecisionStatus.PENDING.value
    )
    project: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    token_id: Mapped[str | None] = mapped_column(Text)
    instance: Mapped[str | None] = mapped_column(Text)
    service: Mapped[InstanceType | None] = mapped_column(instance_type_enum)
    tool: Mapped[str | None] = mapped_column(Text)
    # Exact arguments, needed to execute on approval. They come from the agent,
    # never from the vault.
    args: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    args_hash: Mapped[str | None] = mapped_column(Text)
    # route: {"message", "candidates": [{project, confidence}...], "task": {...}};
    # plan: {"plan", "worker_prompt"}; question: {"question", "open_issues"?}.
    payload: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    # {"choice": ...} or {"text": ...}; who and how are below.
    answer: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    answered_via: Mapped[str | None] = mapped_column(Text)  # telegram, cli, api
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL", name="fk_decisions_task")
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL", name="fk_decisions_thread")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", name="fk_decisions_message")
    )
    # State captured at hold time that must still hold at approval (e.g. n8n updatedAt).
    precondition: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    decided_by: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Snoozed: out of Approvals and its notifications until then (never past expires_at).
    snoozed_until: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditEvent(Base):
    """Append-only (a trigger rejects UPDATE and DELETE). Args are redacted."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_project_at", "project", "at"),
        Index("ix_audit_events_instance_at", "instance", "at"),
        Index("ix_audit_events_outcome_at", "outcome", "at"),
    )

    id: Mapped[int] = mapped_column(BigID, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    # Null only when the token itself was refused: an unverified token names no one.
    project: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    token_id: Mapped[str | None] = mapped_column(Text)
    instance: Mapped[str | None] = mapped_column(Text)
    service: Mapped[InstanceType | None] = mapped_column(instance_type_enum)
    tool: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[AuditOutcome] = mapped_column(audit_outcome_enum)
    reason: Mapped[str | None] = mapped_column(Text)
    args: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    args_hash: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id", name="fk_audit_events_decision")
    )


class Task(Base):
    """A unit of work for one project. Each run (initial or resume) is a WorkerSession."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("max_turns > 0", name="ck_tasks_max_turns_positive"),
        CheckConstraint("max_runtime_s > 0", name="ck_tasks_max_runtime_positive"),
        CheckConstraint(
            "budget_usd IS NULL OR (budget_usd > 0 AND auth_mode = 'api_key')",
            name="ck_tasks_budget_needs_api_key",
        ),
        CheckConstraint(
            "review_round >= 0 AND review_round <= 3", name="ck_tasks_review_round_range"
        ),
        Index("ix_tasks_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    project: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        task_status_enum, server_default=TaskStatus.QUEUED.value
    )
    auth_mode: Mapped[AuthMode] = mapped_column(auth_mode_enum)
    max_turns: Mapped[int] = mapped_column(Integer)
    max_runtime_s: Mapped[int] = mapped_column(Integer)
    budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4, asdecimal=False))
    git_branch: Mapped[str | None] = mapped_column(Text)
    git_commit: Mapped[str | None] = mapped_column(Text)
    changed_files: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    stop_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # hidden, searchable
    # Set when the router created the task.
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("threads.id", ondelete="SET NULL", name="fk_tasks_thread")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL", name="fk_tasks_message")
    )
    kind: Mapped[TaskKind | None] = mapped_column(task_kind_enum)
    risk: Mapped[TaskRisk | None] = mapped_column(task_risk_enum)
    complexity: Mapped[Complexity | None] = mapped_column(complexity_enum)
    outcome: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    # Architect loop (kind = feature) only.
    phase: Mapped[ArchitectPhase | None] = mapped_column(architect_phase_enum)
    review_round: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    failed_check_rounds: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # A service's plan stopped part of this: why, and it isn't a failed attempt.
    plan_limit: Mapped[str | None] = mapped_column(Text)
    worker_prompt: Mapped[str | None] = mapped_column(Text)
    # The project agent it runs as (its instructions, connections, model): see agents.
    agent_id: Mapped[uuid.UUID | None] = mapped_column()

    sessions: Mapped[list["WorkerSession"]] = relationship(
        back_populates="task", order_by="WorkerSession.ordinal", passive_deletes=True
    )


class WorkerSession(Base):
    """One worker container run. A resume adds a row with the same sdk_session_id."""

    __tablename__ = "sessions"
    __table_args__ = (
        UniqueConstraint("task_id", "ordinal", name="uq_sessions_task_ordinal"),
        Index("ix_sessions_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    task_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE", name="fk_sessions_task")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[TaskStatus] = mapped_column(
        task_status_enum, server_default=TaskStatus.QUEUED.value
    )
    role: Mapped[SessionRole] = mapped_column(
        session_role_enum, server_default=SessionRole.WORKER.value
    )
    model: Mapped[str | None] = mapped_column(Text)
    # Architect sessions: the loop step they asked for (submit_plan, dispatch_worker, ...).
    action: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    # Worker turns in the architect loop: {"passed": bool, "results": [...]}.
    checks: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    # What a service's plan refused in this session: {"connection/tool": what it said}.
    plan_refused: Mapped[dict[str, str] | None] = mapped_column(Doc)
    sdk_session_id: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(Text)
    state_volume: Mapped[str] = mapped_column(Text)
    token_jti: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    turns: Mapped[int | None] = mapped_column(Integer)
    # The SDK's own estimate from list prices; not billing data.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False))
    usage: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    model_usage: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    result_subtype: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    task: Mapped[Task] = relationship(back_populates="sessions")


class SessionEvent(Base):
    """One JSON line from a worker's stdout, scrubbed. Deleted after `retention_days`."""

    __tablename__ = "session_events"
    __table_args__ = (
        UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),
        Index("ix_session_events_at", "at"),  # the retention job
    )

    id: Mapped[int] = mapped_column(BigID, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE", name="fk_session_events_session")
    )
    seq: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(Doc)


class Pass(Base):
    """A way for an AI app on this computer (Claude Desktop, Claude Code, Cursor) to use one
    project's connections through the gateway, like a task does. Its token is in the vault;
    only its hash is here. Revoking it stops it at once (services.passes)."""

    __tablename__ = "passes"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(63), unique=True)
    project_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE", name="fk_passes_project")
    )
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    revoked_by: Mapped[str | None] = mapped_column(Text)

    project: Mapped["Project"] = relationship()


class TokenRevocation(Base):
    """Revoked worker tokens, by jti. The gateway refuses any token listed here."""

    __tablename__ = "token_revocations"

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    reason: Mapped[str] = mapped_column(Text)
    revoked_by: Mapped[str] = mapped_column(Text)
    # After this the token is dead anyway, so the row can be cleaned up.
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Thread(Base):
    """A conversation with Ringkeeper: the user's messages and the router's replies."""

    __tablename__ = "threads"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    title: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)  # cli, api, web, telegram
    # For a Telegram request: the chat it came from, the only one its notices go to.
    origin_chat_id: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())  # hidden, searchable

    messages: Mapped[list["Message"]] = relationship(
        back_populates="thread", order_by="Message.created_at", passive_deletes=True
    )


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_thread_created_at", "thread_id", "created_at"),
        # The router's queue: user messages not routed yet.
        Index(
            "ix_messages_unrouted",
            "created_at",
            postgresql_where=text("role = 'user' AND routed_at IS NULL"),
            sqlite_where=text("role = 'user' AND routed_at IS NULL"),
        ),
        CheckConstraint("input IN ('text', 'voice')", name="ck_messages_input"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    thread_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE", name="fk_messages_thread")
    )
    role: Mapped[MessageRole] = mapped_column(message_role_enum)
    author: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    # Files dropped on the request (services.attachments).
    attachments: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    # How the user gave it: typed, or a voice note (only its transcript is kept).
    input: Mapped[str] = mapped_column(Text, server_default=text("'text'"))
    # The router's validated output for this message.
    routing: Mapped[dict[str, Any] | None] = mapped_column(Doc)
    routed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    route_error: Mapped[str | None] = mapped_column(Text)
    # Sent for later: the router leaves it until then. Canceled: it never runs.
    run_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    canceled_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())

    thread: Mapped[Thread] = relationship(back_populates="messages")


class RoutingExample(Base):
    """A routing answer to remember: this phrasing goes to this project."""

    __tablename__ = "routing_examples"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    # Lowercased, whitespace-collapsed request text; an exact match routes directly.
    normalized: Mapped[str] = mapped_column(Text, unique=True)
    text: Mapped[str] = mapped_column(Text)
    project: Mapped[str] = mapped_column(Text)
    kind: Mapped[TaskKind | None] = mapped_column(task_kind_enum)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="SET NULL", name="fk_routing_examples_decision")
    )
    created_by: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Notification(Base):
    """Something sent to a channel (Telegram), so it's sent once and can be updated later.

    About exactly one of: a decision, a routed message (what the router did
    with a Telegram request) or a finished task (from a Telegram request).
    """

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint("decision_id", "channel", "chat_id", name="uq_notifications_target"),
        UniqueConstraint("message_id", "channel", "chat_id", name="uq_notifications_message"),
        UniqueConstraint("task_id", "channel", "chat_id", name="uq_notifications_task"),
        CheckConstraint(
            "num_nonnulls(decision_id, message_id, task_id) = 1",
            name="ck_notifications_one_subject",
        ),
    )

    id: Mapped[int] = mapped_column(BigID, primary_key=True, autoincrement=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE", name="fk_notifications_decision")
    )
    message_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE", name="fk_notifications_message")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE", name="fk_notifications_task")
    )
    channel: Mapped[str] = mapped_column(Text)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    external_id: Mapped[int | None] = mapped_column(BigInteger)  # Telegram message_id
    # Slack and Discord: "<channel id>/<message ts or id>" (chat_id is 0 there).
    external_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text)  # sent, failed, closed
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class VoiceConfirmation(Base):
    """An "I heard: ..." waiting for Confirm or Redo on Telegram.

    Only the transcript is stored, never audio, and it is cleared once the
    confirmation is redone or expires. A confirmed answer lives on in its
    decision, a confirmed request in its message.
    """

    __tablename__ = "voice_confirmations"
    __table_args__ = (
        CheckConstraint("kind IN ('answer', 'request')", name="ck_voice_confirmations_kind"),
        CheckConstraint(
            "(kind = 'answer') = (decision_id IS NOT NULL)",
            name="ck_voice_confirmations_answer_has_decision",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'redone', 'expired', 'used')",
            name="ck_voice_confirmations_status",
        ),
        CheckConstraint(
            "(status = 'pending') = (resolved_at IS NULL)",
            name="ck_voice_confirmations_resolved_iff_not_pending",
        ),
        Index(
            "ix_voice_confirmations_pending",
            "expires_at",
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(Text)  # answer (to a question), request (a new message)
    chat_id: Mapped[int] = mapped_column(BigInteger)
    author: Mapped[str] = mapped_column(Text)  # telegram:<id> (@handle)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE", name="fk_voice_confirmations_decision")
    )
    transcript: Mapped[str | None] = mapped_column(Text)
    external_id: Mapped[int | None] = mapped_column(BigInteger)  # the "I heard" message
    # The Telegram user who sent the voice note: only they can confirm or redo it.
    user_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(Text, server_default=text("'pending'"))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class Channel(TimestampMixin, Base):
    """A Slack or Discord notification channel: your own app or bot (tokens in the
    vault), where it posts, who may answer from it, and what it sends (prefs).
    Telegram's prefs live in settings_kv. See ringkeeper.channels."""

    __tablename__ = "channels"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    kind: Mapped[str] = mapped_column(Text)  # slack, discord
    name: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    # target: a channel id or a user id (a DM); approver: the one user id whose
    # buttons count; plus what setup learned (workspace, bot, app id).
    config: Mapped[dict[str, Any]] = mapped_column(Doc, server_default="{}")
    prefs: Mapped[dict[str, Any]] = mapped_column(Doc, server_default="{}")
    # Only what happens after this is sent: turning a channel on never sends the past.
    since: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class ModelUsage(Base):
    """One model request's tokens, counted by the model proxy."""

    __tablename__ = "model_usage"
    __table_args__ = (Index("ix_model_usage_session", "session_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE", name="fk_model_usage_session")
    )
    project: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(BigInteger)
    output_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_read_tokens: Mapped[int] = mapped_column(BigInteger)
    cache_write_tokens: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, server_default=func.now())


class Setting(Base):
    """Small runtime state (e.g. the Telegram update offset)."""

    __tablename__ = "settings_kv"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(Doc)


class Owner(Base):
    """The one owner account of the web app (single row)."""

    __tablename__ = "owner"
    __table_args__ = (CheckConstraint("id = 1", name="ck_owner_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, server_default=text("1"))
    password_hash: Mapped[str] = mapped_column(Text)  # argon2id
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    password_changed_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class OwnerSession(Base):
    """A signed-in browser. Only a hash of the session token is stored."""

    __tablename__ = "owner_sessions"

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    method: Mapped[str] = mapped_column(Text)  # password, passkey
    user_agent: Mapped[str | None] = mapped_column(Text)
    # Last time the owner proved it was them (password, or a passkey with user
    # verification): sensitive actions need this to be recent.
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Passkey(Base):
    """A WebAuthn credential registered by the owner."""

    __tablename__ = "passkeys"
    __table_args__ = (UniqueConstraint("credential_id", name="uq_passkeys_credential_id"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary)
    public_key: Mapped[bytes] = mapped_column(LargeBinary)
    sign_count: Mapped[int] = mapped_column(BigInteger, server_default=text("0"))
    name: Mapped[str] = mapped_column(Text)
    transports: Mapped[list[str]] = mapped_column(TextList, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class LoginAttempt(Base):
    """Sign-in and setup-code attempts, for rate limiting (per client and overall)."""

    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_key_at", "key", "at"),)

    id: Mapped[int] = mapped_column(BigID, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(Text)  # e.g. "ip:203.0.113.7", "all"
    ok: Mapped[bool] = mapped_column(Boolean)
    at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
