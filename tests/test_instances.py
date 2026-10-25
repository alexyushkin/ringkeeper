import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ringkeeper.errors import AlreadyExistsError, NotFoundError, RuleViolationError
from ringkeeper.models import Env, InstanceType, ProjectInstance
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import bindings, instances, projects
from tests.helpers import make_instance, make_project


def bind(session: Session, project: str, instance: str) -> None:
    bindings.set_binding(session, BindingIn(project=project, instance=instance))


# --- Deleting a bound instance fails unless forced ---


def test_removing_a_bound_instance_fails(session: Session) -> None:
    make_project(session, "acme-web")
    make_project(session, "acme-admin")
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev")
    bind(session, "acme-admin", "acme-supabase-dev")

    with pytest.raises(
        RuleViolationError, match=r"still bound to: acme-admin, acme-web.*--force"
    ) as excinfo:
        instances.remove_instance(session, "acme-supabase-dev")
    assert "Unbind it first" in str(excinfo.value)
    assert instances.find_instance(session, "acme-supabase-dev") is not None


def test_force_removes_instance_and_its_bindings(session: Session) -> None:
    make_project(session, "acme-web")
    make_instance(session, "acme-supabase-dev")
    make_instance(session, "acme-n8n", type=InstanceType.N8N)
    bind(session, "acme-web", "acme-supabase-dev")
    bind(session, "acme-web", "acme-n8n")

    unbound = instances.remove_instance(session, "acme-supabase-dev", force=True)

    assert unbound == ["acme-web"]
    assert instances.find_instance(session, "acme-supabase-dev") is None
    remaining = session.scalars(select(ProjectInstance)).all()
    assert [b.instance.name for b in remaining] == ["acme-n8n"]


def test_removing_an_unbound_instance(session: Session) -> None:
    make_instance(session, "acme-supabase-dev")
    assert instances.remove_instance(session, "acme-supabase-dev") == []
    assert instances.find_instance(session, "acme-supabase-dev") is None


def test_database_restricts_deleting_a_bound_instance(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev")
    with pytest.raises(
        IntegrityError, match=r"fk_project_instances_instance|FOREIGN KEY constraint failed"
    ):
        session.execute(text("DELETE FROM instances WHERE name = 'acme-supabase-dev'"))


def test_removing_a_missing_instance(session: Session) -> None:
    with pytest.raises(NotFoundError, match="instance 'ghost' does not exist"):
        instances.remove_instance(session, "ghost")


# --- Other instance behaviour ---


def test_duplicate_instance_name(session: Session) -> None:
    make_instance(session, "acme-supabase-dev")
    with pytest.raises(AlreadyExistsError, match="already exists"):
        make_instance(session, "acme-supabase-dev")


def test_type_cannot_change_while_bound(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-x")
    bind(session, "acme-web", "acme-x")
    data = InstanceIn(
        name="acme-x",
        type=InstanceType.NEON,
        env=Env.DEV,
        secret_ref="aws-sm://ringkeeper/acme-x",
        owner="acme",
    )
    with pytest.raises(RuleViolationError, match="cannot change the type"):
        instances.update_instance(session, data)


def test_list_instances_filters(session: Session) -> None:
    make_instance(session, "acme-supabase-dev")
    make_instance(session, "acme-supabase-prod", env=Env.PROD)
    make_instance(session, "acme-n8n", type=InstanceType.N8N)
    names = [i.name for i in instances.list_instances(session, type=InstanceType.SUPABASE)]
    assert names == ["acme-supabase-dev", "acme-supabase-prod"]
    assert [i.name for i in instances.list_instances(session, env=Env.PROD)] == [
        "acme-supabase-prod"
    ]


# --- Projects ---


def test_removing_a_project_removes_its_bindings_only(session: Session) -> None:
    make_project(session, "acme-web")
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev")

    assert projects.remove_project(session, "acme-web") == 1
    assert projects.find_project(session, "acme-web") is None
    assert instances.find_instance(session, "acme-supabase-dev") is not None
    assert session.scalars(select(ProjectInstance)).all() == []


def test_duplicate_project_name(session: Session) -> None:
    make_project(session)
    with pytest.raises(AlreadyExistsError, match="project 'acme-web' already exists"):
        make_project(session)


@pytest.mark.parametrize("name", ["Acme", "acme_web", "-acme", "acme-", "", "a" * 64])
def test_names_must_be_slugs(session: Session, name: str) -> None:
    with pytest.raises(ValueError, match="should match pattern"):
        make_project(session, name)
