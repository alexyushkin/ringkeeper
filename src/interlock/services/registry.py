"""Declarative registry files: diff, apply and export.

`apply` never deletes. Anything missing from the file is left alone. It runs
every write through the other services, so a dry run hits exactly the same
rules as a real one; the caller decides whether to commit.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Self, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from interlock.errors import InterlockError
from interlock.models import Instance, Project, ProjectInstance
from interlock.schemas import BindingIn, CleanList, InstanceIn, ProjectIn, Slug
from interlock.services import bindings, instances, projects

PROJECT_FIELDS = ("owner", "description", "aliases", "repo_url", "workspace_path", "autonomy")
INSTANCE_FIELDS = ("type", "env", "base_url", "secret_ref", "owner")
BINDING_FIELDS = ("default", "allowed_tools", "preapproved_writes")


class RegistryBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance: Slug
    default: bool = False
    allowed_tools: CleanList = Field(default_factory=list[str])
    preapproved_writes: CleanList = Field(default_factory=list[str])


class RegistryProject(ProjectIn):
    bindings: list[RegistryBinding] = Field(default_factory=list[RegistryBinding])

    @model_validator(mode="after")
    def _unique_bindings(self) -> Self:
        _require_unique([b.instance for b in self.bindings], f"bindings of project '{self.name}'")
        return self


class RegistryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    instances: list[InstanceIn] = Field(default_factory=list[InstanceIn])
    projects: list[RegistryProject] = Field(default_factory=list[RegistryProject])

    @model_validator(mode="after")
    def _unique_names(self) -> Self:
        _require_unique([i.name for i in self.instances], "instances")
        _require_unique([p.name for p in self.projects], "projects")
        return self


def _require_unique(names: list[str], where: str) -> None:
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise ValueError(f"duplicate names in {where}: {', '.join(duplicates)}")


# --- Plan ---

Kind = Literal["instance", "project", "binding"]
Action = Literal["create", "update", "unchanged"]


@dataclass(frozen=True)
class FieldChange:
    name: str
    old: object
    new: object


@dataclass(frozen=True)
class Change:
    kind: Kind
    key: str
    action: Action
    fields: tuple[FieldChange, ...] = ()


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list[Change])

    @property
    def has_changes(self) -> bool:
        return any(c.action != "unchanged" for c in self.changes)

    def count(self, action: Action) -> int:
        return sum(1 for c in self.changes if c.action == action)


def _diff(
    current: object, desired: dict[str, Any], names: tuple[str, ...]
) -> tuple[FieldChange, ...]:
    changes: list[FieldChange] = []
    for name in names:
        old = getattr(current, name)
        new = desired[name]
        if old != new:
            changes.append(FieldChange(name, old, new))
    return tuple(changes)


def _change(
    kind: Kind, key: str, current: object | None, desired: dict[str, Any], names: tuple[str, ...]
) -> Change:
    if current is None:
        return Change(kind, key, "create")
    fields = _diff(current, desired, names)
    return Change(kind, key, "update" if fields else "unchanged", fields)


@dataclass(frozen=True)
class _BindingView:
    """A binding seen through registry field names (`default`, not `is_default`)."""

    default: bool
    allowed_tools: list[str]
    preapproved_writes: list[str]

    @classmethod
    def of(cls, binding: ProjectInstance) -> "_BindingView":
        return cls(
            binding.is_default, list(binding.allowed_tools), list(binding.preapproved_writes)
        )


def binding_key(project: str, instance: str) -> str:
    return f"{project} -> {instance}"


# --- Apply ---


def apply_registry(session: Session, registry: RegistryFile) -> Plan:
    """Upsert everything in the file. Returns what changed.

    Writes happen in an order that lets one file move a default between
    instances or demote a default before its instance becomes prod:
    demoted defaults first, then instances, projects, plain bindings, and
    finally new defaults.
    """
    plan = Plan()
    steps: list[tuple[int, Callable[[], object]]] = []

    def step(order: int, change: Change, action: Callable[[], object]) -> None:
        plan.changes.append(change)
        if change.action != "unchanged":
            steps.append((order, _labelled(change, action)))

    for data in registry.instances:
        current: Instance | None = instances.find_instance(session, data.name)
        change = _change("instance", data.name, current, data.model_dump(), INSTANCE_FIELDS)
        write = instances.create_instance if current is None else instances.update_instance
        step(1, change, lambda write=write, data=data: write(session, data))

    for project_data in registry.projects:
        project: Project | None = projects.find_project(session, project_data.name)
        project_in = ProjectIn.model_validate(project_data.model_dump(exclude={"bindings"}))
        change = _change(
            "project", project_in.name, project, project_in.model_dump(), PROJECT_FIELDS
        )
        write_project = projects.create_project if project is None else projects.update_project
        step(2, change, lambda write=write_project, data=project_in: write(session, data))

        for rb in project_data.bindings:
            existing = _existing_binding(session, project, rb.instance)
            view = None if existing is None else _BindingView.of(existing)
            change = _change(
                "binding",
                binding_key(project_data.name, rb.instance),
                view,
                rb.model_dump(),
                BINDING_FIELDS,
            )
            if view is not None and view.default and not rb.default:
                order = 0
            else:
                order = 4 if rb.default else 3
            binding_data = BindingIn(
                project=project_data.name,
                instance=rb.instance,
                is_default=rb.default,
                allowed_tools=rb.allowed_tools,
                preapproved_writes=rb.preapproved_writes,
            )
            step(order, change, lambda d=binding_data: bindings.set_binding(session, d))

    for _, action in sorted(steps, key=lambda s: s[0]):
        action()
    return plan


def _existing_binding(
    session: Session, project: Project | None, instance_name: str
) -> ProjectInstance | None:
    if project is None:
        return None
    instance = instances.find_instance(session, instance_name)
    return None if instance is None else bindings.find_binding(project, instance)


def _labelled(change: Change, action: Callable[[], object]) -> Callable[[], object]:
    def run() -> object:
        try:
            return action()
        except InterlockError as error:
            raise type(error)(f"{change.kind} {change.key}: {error}") from error

    return run


# --- Files ---


class RegistryFileError(InterlockError):
    pass


def load_registry_file(path: Path) -> RegistryFile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RegistryFileError(f"cannot read {path}: {error.strerror}") from error
    except yaml.YAMLError as error:
        raise RegistryFileError(f"{path} is not valid YAML: {error}") from error
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise RegistryFileError(f"{path} must contain a mapping at the top level")
    return RegistryFile.model_validate(raw)


def export_registry(session: Session) -> RegistryFile:
    exported_instances = [
        InstanceIn.model_validate(i, from_attributes=True)
        for i in instances.list_instances(session)
    ]
    exported_projects: list[RegistryProject] = []
    for summary in projects.list_projects(session):
        project = projects.get_project(session, summary.name)
        exported_projects.append(
            RegistryProject(
                **ProjectIn.model_validate(project, from_attributes=True).model_dump(),
                bindings=[
                    RegistryBinding(
                        instance=b.instance.name,
                        default=b.is_default,
                        allowed_tools=b.allowed_tools,
                        preapproved_writes=b.preapproved_writes,
                    )
                    for b in bindings.sorted_bindings(project)
                ],
            )
        )
    return RegistryFile(instances=exported_instances, projects=exported_projects)


def dump_registry(registry: RegistryFile) -> str:
    data = registry.model_dump(mode="json", exclude_none=True)
    header = "# Generated by `interlock registry export`.\n"
    return header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def format_value(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, list):
        return "[" + ", ".join(str(v) for v in cast(list[object], value)) + "]"
    return repr(value) if isinstance(value, str) else str(value)
