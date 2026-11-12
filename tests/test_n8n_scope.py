"""A service you host for several clients: your own n8n, linked to each client's project with
a narrowed scope. The gateway checks it on every call: another client's workflows aren't
listed, read, changed or held for approval, and their runs don't show."""

from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, AuditOutcome, Decision, Env, InstanceType
from ringkeeper.schemas import InstanceIn
from ringkeeper.services import instances
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import Vault, get_vault
from tests.fake_n8n import API_KEY, FakeN8n
from tests.helpers import bind, make_instance, make_project

pytestmark = pytest.mark.anyio

GLOBEX = Caller("globex-portal", "session-globex", "token-globex")
HOSTED = "agency-n8n"


@pytest.fixture
def vault(database_url: str) -> Vault:
    vault = get_vault()
    vault.store("file://agency/n8n", API_KEY)
    return vault


@pytest.fixture
def projects(vault: Vault) -> None:
    with session_scope() as session:
        make_project(session, "acme-web")
        make_project(session, "globex-portal", owner="globex")
        make_instance(
            session, "acme-n8n", type=InstanceType.N8N, env=Env.PROD,
            secret_ref="file://agency/n8n", base_url="https://n8n.acme.example",
        )  # fmt: skip
    bind("acme-web", "acme-n8n")


@pytest.fixture
def hosted(projects: None, fake_n8n: FakeN8n) -> FakeN8n:
    """Your n8n, with workflows for two clients, linked to Globex's project for its tag."""
    fake_n8n.workflows.clear()
    fake_n8n.add_workflow("g1", "Globex leads", active=True, tags=("globex",))
    fake_n8n.add_workflow("g2", "Globex report", tags=("globex", "weekly"))
    fake_n8n.add_workflow("a1", "Acme invoices", active=True, tags=("acme",))
    fake_n8n.executions.extend(
        [
            {"id": "11", "workflowId": "g1", "status": "success"},
            {"id": "12", "workflowId": "a1", "status": "error"},
        ]
    )
    with session_scope() as session:
        make_instance(
            session, HOSTED, type=InstanceType.N8N, env=Env.PROD, secret_ref="file://agency/n8n",
            base_url="https://n8n.agency.example", shared=True, owner=instances.SHARED_OWNER,
        )  # fmt: skip
    bind("globex-portal", HOSTED, scope={"tag": "globex"})
    return fake_n8n


def call(tool: str, **args: Any) -> ToolCall:
    return ToolCall(GLOBEX, InstanceType.N8N, tool, HOSTED, args)


def last_audit() -> AuditEvent:
    with session_scope() as session:
        return session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).one()


def held() -> int:
    with session_scope() as session:
        return len(session.scalars(select(Decision)).all())


async def test_a_client_s_project_lists_only_its_workflows(hosted: FakeN8n, vault: Vault) -> None:
    result = await run_call(call("list_workflows"), vault)
    assert [w["name"] for w in result["workflows"]] == ["Globex leads", "Globex report"]
    assert "Acme" not in str(result)  # not even a name
    assert result["limited_to"] == "tagged 'globex'"
    assert hosted.requests[-1].url.params["tags"] == "globex"  # n8n filters it too


async def test_the_list_is_checked_even_if_n8n_ignores_the_filter(
    hosted: FakeN8n, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An older n8n that doesn't filter by tag still shows nothing of another client's."""
    real = hosted.handle

    def unfiltered(request: Any) -> Any:
        request.url = request.url.copy_remove_param("tags")
        return real(request)

    monkeypatch.setattr(hosted, "handle", unfiltered)
    result = await run_call(call("list_workflows"), vault)
    assert [w["id"] for w in result["workflows"]] == ["g1", "g2"]


async def test_another_client_s_workflow_is_refused(hosted: FakeN8n, vault: Vault) -> None:
    with pytest.raises(RefusalError, match="only reaches workflows tagged 'globex'"):
        await run_call(call("get_workflow", workflow_id="a1"), vault)
    assert last_audit().outcome is AuditOutcome.REFUSED
    found = await run_call(call("get_workflow", workflow_id="g2"), vault)
    assert found["name"] == "Globex report"


async def test_a_change_to_another_client_s_workflow_is_refused_not_held(
    hosted: FakeN8n, vault: Vault
) -> None:
    with pytest.raises(RefusalError, match="isn't one this project may use"):
        await run_call(call("deactivate_workflow", workflow_id="a1"), vault)
    assert held() == 0  # never waits for an approval that would only refuse it
    assert hosted.workflows["a1"]["active"] is True
    result = await run_call(call("deactivate_workflow", workflow_id="g1"), vault)
    assert result["status"] == "held_for_approval"  # its own: production, so it asks


async def test_runs_show_only_the_client_s_workflows(hosted: FakeN8n, vault: Vault) -> None:
    page = await run_call(call("list_executions"), vault)
    assert [e["id"] for e in page["executions"]] == ["11"]
    assert page["total"] == 1  # the count leaks nothing about the other client's runs
    with pytest.raises(RefusalError, match="execution 12 isn't one"):
        await run_call(call("get_execution", execution_id="12"), vault)
    with pytest.raises(RefusalError, match="workflow a1 isn't one"):
        await run_call(call("list_executions", workflow_id="a1"), vault)
    with pytest.raises(RefusalError, match="execution 12 isn't one"):
        await run_call(call("retry_execution", execution_id="12"), vault)
    assert held() == 0


async def test_an_n8n_project_scope_works_on_its_own(hosted: FakeN8n, vault: Vault) -> None:
    hosted.add_workflow("p1", "In the client's n8n project", tags=(), project="p-globex")
    bind("globex-portal", HOSTED, scope={"project": "p-globex"})
    result = await run_call(call("list_workflows"), vault)
    assert [w["id"] for w in result["workflows"]] == ["p1"]
    with pytest.raises(RefusalError, match="in n8n project p-globex"):
        await run_call(call("get_workflow", workflow_id="g1"), vault)


def test_a_client_s_connection_can_become_your_own_tool(projects: None) -> None:
    """Moving acme-n8n to your own tools keeps its links; then it can be linked, narrowed,
    to another client's project."""
    with session_scope() as session:
        found = instances.get_instance(session, "acme-n8n")
        data = InstanceIn.model_validate(
            {
                "name": found.name, "type": found.type, "env": found.env,
                "secret_ref": found.secret_ref, "base_url": found.base_url,
                "owner": instances.SHARED_OWNER, "shared": True,
            }
        )  # fmt: skip
        instances.update_instance(session, data)
    bind("globex-portal", "acme-n8n", scope={"tag": "globex"})
    with session_scope() as session:
        moved = instances.get_instance(session, "acme-n8n")
        assert moved.shared
        assert sorted(b.project.name for b in moved.bindings) == ["acme-web", "globex-portal"]
