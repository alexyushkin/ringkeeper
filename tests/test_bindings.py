import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from interlock.errors import NotFoundError, RuleViolationError
from interlock.models import Env, InstanceType, ProjectInstance
from interlock.schemas import BindingIn, InstanceIn
from interlock.services import bindings, instances
from interlock.services.projects import get_project
from tests.helpers import make_instance, make_project


def bind(session: Session, project: str, instance: str, **fields: object) -> ProjectInstance:
    data = BindingIn.model_validate({"project": project, "instance": instance, **fields})
    binding, _ = bindings.set_binding(session, data)
    return binding


# --- At most one default instance per type per project ---


def test_second_default_of_same_type_is_rejected(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    make_instance(session, "acme-supabase-staging", env=Env.STAGING)
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)

    with pytest.raises(RuleViolationError, match="already has a default supabase instance"):
        bind(session, "acme-web", "acme-supabase-staging", is_default=True)


def test_defaults_of_different_types_coexist(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    make_instance(session, "acme-n8n", type=InstanceType.N8N)
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    bind(session, "acme-web", "acme-n8n", is_default=True)

    project_bindings = bindings.sorted_bindings(get_project(session, "acme-web"))
    assert [(b.instance.name, b.is_default) for b in project_bindings] == [
        ("acme-n8n", True),
        ("acme-supabase-dev", True),
    ]


def test_default_is_per_project(session: Session) -> None:
    make_project(session, "acme-web")
    make_project(session, "acme-admin")
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    bind(session, "acme-admin", "acme-supabase-dev", is_default=True)


def test_default_can_move_after_unsetting(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    make_instance(session, "acme-supabase-staging", env=Env.STAGING)
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    bind(session, "acme-web", "acme-supabase-dev", is_default=False)
    binding = bind(session, "acme-web", "acme-supabase-staging", is_default=True)
    assert binding.is_default


def test_rebinding_the_current_default_as_default_is_fine(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    binding = bind(session, "acme-web", "acme-supabase-dev", is_default=True, allowed_tools=["sql"])
    assert binding.allowed_tools == ["sql"]


def test_database_enforces_one_default_per_type(session: Session) -> None:
    project = make_project(session)
    first = make_instance(session, "acme-supabase-dev")
    second = make_instance(session, "acme-supabase-staging", env=Env.STAGING)
    for instance in (first, second):
        session.add(ProjectInstance(project=project, instance=instance, is_default=True))
    with pytest.raises(IntegrityError, match="uq_project_instances_one_default_per_type"):
        session.flush()


# --- A prod instance can be bound but never set as default ---


def test_prod_instance_can_be_bound(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-prod", env=Env.PROD)
    binding = bind(session, "acme-web", "acme-supabase-prod")
    assert binding.instance_env == Env.PROD
    assert not binding.is_default


def test_prod_instance_cannot_be_default(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-prod", env=Env.PROD)
    with pytest.raises(RuleViolationError, match="is prod and cannot be a default"):
        bind(session, "acme-web", "acme-supabase-prod", is_default=True)


def test_default_instance_cannot_be_changed_to_prod(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    data = InstanceIn.model_validate(
        {
            "name": "acme-supabase-dev",
            "type": "supabase",
            "env": "prod",
            "secret_ref": "aws-sm://interlock/acme-supabase-dev",
            "owner": "acme",
        }
    )
    with pytest.raises(RuleViolationError, match="cannot make instance 'acme-supabase-dev' prod"):
        instances.update_instance(session, data)


def test_non_default_binding_follows_env_change(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-x")
    binding = bind(session, "acme-web", "acme-supabase-x")
    data = InstanceIn.model_validate(
        {
            "name": "acme-supabase-x",
            "type": "supabase",
            "env": "prod",
            "secret_ref": "aws-sm://interlock/acme-supabase-x",
            "owner": "acme",
        }
    )
    instances.update_instance(session, data)
    assert binding.instance_env == Env.PROD


def test_database_rejects_prod_default_directly(session: Session) -> None:
    project = make_project(session)
    instance = make_instance(session, "acme-supabase-prod", env=Env.PROD)
    session.add(ProjectInstance(project=project, instance=instance, is_default=True))
    with pytest.raises(IntegrityError, match="ck_project_instances_prod_not_default"):
        session.flush()


def test_database_rejects_promoting_a_default_to_prod(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev", is_default=True)
    # The composite FK cascades env into the binding, where the CHECK catches it.
    with pytest.raises(IntegrityError, match="ck_project_instances_prod_not_default"):
        session.execute(text("UPDATE instances SET env = 'prod' WHERE name = 'acme-supabase-dev'"))


# --- Binding a missing project or instance fails clearly ---


def test_binding_missing_project(session: Session) -> None:
    make_instance(session, "acme-supabase-dev")
    with pytest.raises(NotFoundError, match=r"^cannot bind: project 'nope' does not exist$"):
        bind(session, "nope", "acme-supabase-dev")


def test_binding_missing_instance(session: Session) -> None:
    make_project(session)
    with pytest.raises(NotFoundError, match=r"^cannot bind: instance 'nope' does not exist$"):
        bind(session, "acme-web", "nope")


def test_binding_both_missing(session: Session) -> None:
    with pytest.raises(
        NotFoundError, match=r"^cannot bind: project 'ghost' and instance 'nope' do not exist$"
    ):
        bind(session, "ghost", "nope")


# --- Binding settings and unbind ---


def test_rebinding_replaces_settings(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev", allowed_tools=["sql", "storage"])
    binding = bind(session, "acme-web", "acme-supabase-dev", preapproved_writes=["insert:logs"])
    assert binding.allowed_tools == []
    assert binding.preapproved_writes == ["insert:logs"]


def test_tool_lists_are_cleaned(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    binding = bind(session, "acme-web", "acme-supabase-dev", allowed_tools=[" sql", "sql", ""])
    assert binding.allowed_tools == ["sql"]


def test_unbind(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    bind(session, "acme-web", "acme-supabase-dev")
    bindings.remove_binding(session, "acme-web", "acme-supabase-dev")
    assert get_project(session, "acme-web").bindings == []


def test_unbind_when_not_bound(session: Session) -> None:
    make_project(session)
    make_instance(session, "acme-supabase-dev")
    with pytest.raises(NotFoundError, match="is not bound to project 'acme-web'"):
        bindings.remove_binding(session, "acme-web", "acme-supabase-dev")
