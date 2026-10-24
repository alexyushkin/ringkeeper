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
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

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
    GOOGLE = "google"


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


class AuditOutcome(enum.StrEnum):
    REFUSED = "refused"
    HELD = "held"
    EXECUTED = "executed"
    FAILED = "failed"


decision_status_enum = Enum(DecisionStatus, name="decision_status", values_callable=_enum_values)
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


TERMINAL_STATUSES = frozenset(
    {
        TaskStatus.SUCCEEDED,
        TaskStatus.FAILED,
        TaskStatus.TIMED_OUT,
        TaskStatus.STOPPED,
        TaskStatus.BUDGET_EXCEEDED,
    }
)

auth_mode_enum = Enum(AuthMode, name="auth_mode", values_callable=_enum_values)
task_status_enum = Enum(TaskStatus, name="task_status", values_callable=_enum_values)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (CheckConstraint(f"name ~ '{SLUG_PATTERN}'", name="ck_projects_name_slug"),)

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(63), unique=True)
    owner: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text, server_default="")
    aliases: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    repo_url: Mapped[str | None] = mapped_column(Text)
    workspace_path: Mapped[str | None] = mapped_column(Text)
    autonomy: Mapped[Autonomy] = mapped_column(
        autonomy_enum, server_default=Autonomy.STANDARD.value
    )

    bindings: Mapped[list["ProjectInstance"]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


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
    type: Mapped[InstanceType] = mapped_column(instance_type_enum)
    env: Mapped[Env] = mapped_column(instance_env_enum)
    base_url: Mapped[str | None] = mapped_column(Text)
    secret_ref: Mapped[str] = mapped_column(Text)
    owner: Mapped[str] = mapped_column(Text)

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
    allowed_tools: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    preapproved_writes: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")

    project: Mapped[Project] = relationship(back_populates="bindings")
    instance: Mapped[Instance] = relationship(back_populates="bindings")


class Decision(Base):
    """A held prod write, waiting for a human. Names are stored as text so the
    history survives later changes to the registry; approval re-checks them."""

    __tablename__ = "decisions"
    __table_args__ = (
        CheckConstraint(
            "(status = 'pending') = (decided_at IS NULL)",
            name="ck_decisions_decided_iff_not_pending",
        ),
        CheckConstraint("expires_at > created_at", name="ck_decisions_expiry_after_creation"),
        Index("ix_decisions_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True, server_default=text("gen_random_uuid()")
    )
    status: Mapped[DecisionStatus] = mapped_column(
        decision_status_enum, server_default=DecisionStatus.PENDING.value
    )
    project: Mapped[str] = mapped_column(Text)
    session_id: Mapped[str] = mapped_column(Text)
    token_id: Mapped[str] = mapped_column(Text)
    instance: Mapped[str] = mapped_column(Text)
    service: Mapped[InstanceType] = mapped_column(instance_type_enum)
    tool: Mapped[str] = mapped_column(Text)
    # Exact arguments, needed to execute on approval. They come from the agent,
    # never from the vault.
    args: Mapped[dict[str, Any]] = mapped_column(JSONB)
    args_hash: Mapped[str] = mapped_column(Text)
    # State captured at hold time that must still hold at approval (e.g. n8n updatedAt).
    precondition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str | None] = mapped_column(Text)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuditEvent(Base):
    """Append-only (a trigger rejects UPDATE and DELETE). Args are redacted."""

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_project_at", "project", "at"),
        Index("ix_audit_events_instance_at", "instance", "at"),
        Index("ix_audit_events_outcome_at", "outcome", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Null only when the token itself was refused: an unverified token names no one.
    project: Mapped[str | None] = mapped_column(Text)
    session_id: Mapped[str | None] = mapped_column(Text)
    token_id: Mapped[str | None] = mapped_column(Text)
    instance: Mapped[str | None] = mapped_column(Text)
    service: Mapped[InstanceType | None] = mapped_column(instance_type_enum)
    tool: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[AuditOutcome] = mapped_column(audit_outcome_enum)
    reason: Mapped[str | None] = mapped_column(Text)
    args: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
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
    changed_files: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    stop_requested: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

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
    sdk_session_id: Mapped[str | None] = mapped_column(Text)
    container_id: Mapped[str | None] = mapped_column(Text)
    state_volume: Mapped[str] = mapped_column(Text)
    token_jti: Mapped[str | None] = mapped_column(Text)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    turns: Mapped[int | None] = mapped_column(Integer)
    # The SDK's own estimate from list prices; not billing data.
    cost_usd: Mapped[float | None] = mapped_column(Numeric(12, 6, asdecimal=False))
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    model_usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result_subtype: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="sessions")


class SessionEvent(Base):
    """One JSON line from a worker's stdout, scrubbed."""

    __tablename__ = "session_events"
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_session_events_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE", name="fk_session_events_session")
    )
    seq: Mapped[int] = mapped_column(Integer)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    type: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)


class TokenRevocation(Base):
    """Revoked worker tokens, by jti. The gateway refuses any token listed here."""

    __tablename__ = "token_revocations"

    jti: Mapped[str] = mapped_column(Text, primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reason: Mapped[str] = mapped_column(Text)
    revoked_by: Mapped[str] = mapped_column(Text)
    # After this the token is dead anyway, so the row can be cleaned up.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
