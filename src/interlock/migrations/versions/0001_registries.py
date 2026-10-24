"""Registries: projects, instances and project_instances.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
SECRET_REF_DB_PATTERN = r"^(file|aws-sm)://[^\s?#@]+$"  # noqa: S105 (a regex)
TEXT_ARRAY: postgresql.ARRAY[str] = postgresql.ARRAY(sa.Text())

autonomy = postgresql.ENUM("strict", "standard", "relaxed", name="autonomy", create_type=False)
instance_type = postgresql.ENUM(
    "supabase",
    "n8n",
    "neon",
    "vercel",
    "airtable",
    "google",
    name="instance_type",
    create_type=False,
)
instance_env = postgresql.ENUM("prod", "staging", "dev", name="instance_env", create_type=False)


def _timestamps() -> list[sa.Column[datetime]]:
    return [
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    autonomy.create(bind)
    instance_type.create(bind)
    instance_env.create(bind)

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column("aliases", TEXT_ARRAY, server_default="{}", nullable=False),
        sa.Column("repo_url", sa.Text(), nullable=True),
        sa.Column("worktree_path", sa.Text(), nullable=True),
        sa.Column[str]("autonomy", autonomy, server_default="standard", nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="projects_pkey"),
        sa.UniqueConstraint("name", name="projects_name_key"),
        sa.CheckConstraint(f"name ~ '{SLUG_PATTERN}'", name="ck_projects_name_slug"),
    )

    op.create_table(
        "instances",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(63), nullable=False),
        sa.Column[str]("type", instance_type, nullable=False),
        sa.Column[str]("env", instance_env, nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=False),
        sa.Column("owner", sa.Text(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="instances_pkey"),
        sa.UniqueConstraint("name", name="instances_name_key"),
        sa.UniqueConstraint("id", "type", "env", name="uq_instances_id_type_env"),
        sa.CheckConstraint(f"name ~ '{SLUG_PATTERN}'", name="ck_instances_name_slug"),
        sa.CheckConstraint(
            f"secret_ref ~ '{SECRET_REF_DB_PATTERN}'", name="ck_instances_secret_ref_pointer"
        ),
    )

    op.create_table(
        "project_instances",
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column[str]("instance_type", instance_type, nullable=False),
        sa.Column[str]("instance_env", instance_env, nullable=False),
        sa.Column("is_default", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("allowed_tools", TEXT_ARRAY, server_default="{}", nullable=False),
        sa.Column("preapproved_writes", TEXT_ARRAY, server_default="{}", nullable=False),
        sa.PrimaryKeyConstraint("project_id", "instance_id", name="project_instances_pkey"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_project_instances_project",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id", "instance_type", "instance_env"],
            ["instances.id", "instances.type", "instances.env"],
            name="fk_project_instances_instance",
            onupdate="CASCADE",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "NOT (is_default AND instance_env = 'prod')",
            name="ck_project_instances_prod_not_default",
        ),
    )
    op.create_index(
        "uq_project_instances_one_default_per_type",
        "project_instances",
        ["project_id", "instance_type"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )


def downgrade() -> None:
    op.drop_index("uq_project_instances_one_default_per_type", table_name="project_instances")
    op.drop_table("project_instances")
    op.drop_table("instances")
    op.drop_table("projects")
    bind = op.get_bind()
    instance_env.drop(bind)
    instance_type.drop(bind)
    autonomy.drop(bind)
