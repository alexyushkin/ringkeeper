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

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
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
