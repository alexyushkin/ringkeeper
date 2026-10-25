"""The web app: the owner account, sessions, passkeys and sign-in attempts;
connection test results; the `changed` audit outcome for owner changes; an
index for the retention job; and NOTIFY triggers for live updates.

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Live updates for the web app. Payloads carry ids only; the app fetches the
# rest through the API. Triggers, so every writer (manager, gateway, CLI) counts.
NOTIFY_FUNCTIONS = """
CREATE FUNCTION ringkeeper_notify_session_event() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  PERFORM pg_notify('ringkeeper_events', json_build_object(
    'type', 'session_event', 'session_id', NEW.session_id, 'seq', NEW.seq,
    'task_id', (SELECT task_id FROM sessions WHERE id = NEW.session_id))::text);
  RETURN NEW;
END $$;
CREATE FUNCTION ringkeeper_notify_task() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status
     OR NEW.phase IS DISTINCT FROM OLD.phase THEN
    PERFORM pg_notify('ringkeeper_events', json_build_object(
      'type', 'task', 'task_id', NEW.id, 'status', NEW.status, 'project', NEW.project,
      'thread_id', NEW.thread_id)::text);
  END IF;
  RETURN NEW;
END $$;
CREATE FUNCTION ringkeeper_notify_decision() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.status IS DISTINCT FROM OLD.status
     OR NEW.answer IS DISTINCT FROM OLD.answer THEN
    PERFORM pg_notify('ringkeeper_events', json_build_object(
      'type', 'approval', 'decision_id', NEW.id, 'kind', NEW.kind, 'status', NEW.status,
      'thread_id', NEW.thread_id, 'task_id', NEW.task_id)::text);
  END IF;
  RETURN NEW;
END $$;
CREATE FUNCTION ringkeeper_notify_message() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.routed_at IS DISTINCT FROM OLD.routed_at THEN
    PERFORM pg_notify('ringkeeper_events', json_build_object(
      'type', CASE WHEN TG_OP = 'INSERT' THEN 'message' ELSE 'message_routed' END,
      'message_id', NEW.id, 'thread_id', NEW.thread_id)::text);
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER ringkeeper_notify AFTER INSERT ON session_events
  FOR EACH ROW EXECUTE FUNCTION ringkeeper_notify_session_event();
CREATE TRIGGER ringkeeper_notify AFTER INSERT OR UPDATE ON tasks
  FOR EACH ROW EXECUTE FUNCTION ringkeeper_notify_task();
CREATE TRIGGER ringkeeper_notify AFTER INSERT OR UPDATE ON decisions
  FOR EACH ROW EXECUTE FUNCTION ringkeeper_notify_decision();
CREATE TRIGGER ringkeeper_notify AFTER INSERT OR UPDATE ON messages
  FOR EACH ROW EXECUTE FUNCTION ringkeeper_notify_message();
"""


def _now(name: str) -> sa.Column[datetime]:
    return sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    # ADD VALUE can't run inside the migration's transaction block on older
    # servers; autocommit_block makes it its own statement.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE audit_outcome ADD VALUE IF NOT EXISTS 'changed'")

    op.create_table(
        "owner",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        _now("created_at"),
        _now("password_changed_at"),
        sa.PrimaryKeyConstraint("id", name="owner_pkey"),
        sa.CheckConstraint("id = 1", name="ck_owner_single_row"),
    )
    op.create_table(
        "owner_sessions",
        sa.Column("token_hash", sa.Text(), nullable=False),
        _now("created_at"),
        _now("last_seen_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("token_hash", name="owner_sessions_pkey"),
    )
    op.create_table(
        "passkeys",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("transports", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False),
        _now("created_at"),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="passkeys_pkey"),
        sa.UniqueConstraint("credential_id", name="uq_passkeys_credential_id"),
    )
    op.create_table(
        "login_attempts",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("ok", sa.Boolean(), nullable=False),
        _now("at"),
        sa.PrimaryKeyConstraint("id", name="login_attempts_pkey"),
    )
    op.create_index("ix_login_attempts_key_at", "login_attempts", ["key", "at"])

    op.add_column("instances", sa.Column("last_test_at", sa.DateTime(timezone=True)))
    op.add_column("instances", sa.Column("last_test_ok", sa.Boolean()))
    op.add_column("instances", sa.Column("last_test_message", sa.Text()))
    op.create_index("ix_session_events_at", "session_events", ["at"])
    op.execute(NOTIFY_FUNCTIONS)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return  # SQLite gets the whole 0008 schema at once (storage.sqlite_baseline)
    for table, function in (
        ("messages", "ringkeeper_notify_message"),
        ("decisions", "ringkeeper_notify_decision"),
        ("tasks", "ringkeeper_notify_task"),
        ("session_events", "ringkeeper_notify_session_event"),
    ):
        op.execute(f"DROP TRIGGER ringkeeper_notify ON {table}")
        op.execute(f"DROP FUNCTION {function}()")
    op.drop_index("ix_session_events_at", "session_events")
    for column in ("last_test_message", "last_test_ok", "last_test_at"):
        op.drop_column("instances", column)
    op.drop_index("ix_login_attempts_key_at", "login_attempts")
    for table in ("login_attempts", "passkeys", "owner_sessions", "owner"):
        op.drop_table(table)
    # 'changed' stays in audit_outcome: Postgres can't drop enum values, and
    # audit rows that use it can't be deleted (the log is append-only).
