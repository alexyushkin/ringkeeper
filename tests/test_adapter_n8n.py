import pytest

from ringkeeper import scrub
from ringkeeper.adapters import n8n
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.models import Env
from ringkeeper.vault import Secret
from tests.fake_n8n import API_KEY, FakeN8n

pytestmark = pytest.mark.anyio


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeN8n:
    fake = FakeN8n()
    fake.add_workflow("wf1", "Sync leads", active=True)
    fake.add_workflow("wf2", "Nightly report")
    fake.executions.append({"id": "ex1", "workflowId": "wf1", "status": "success"})
    monkeypatch.setattr(n8n, "transport", fake.transport())
    return fake


def target(api_key: str = API_KEY, base_url: str | None = "https://n8n.acme.example") -> Target:
    return Target("acme-n8n", Env.DEV, base_url, Secret("file://acme/n8n", api_key))


async def test_list_workflows_summarizes(fake: FakeN8n) -> None:
    result = await n8n.list_workflows.handler(target(), active=True)
    assert result == {
        "workflows": [
            {
                "id": "wf1",
                "name": "Sync leads",
                "active": True,
                "updatedAt": fake.workflows["wf1"]["updatedAt"],
                "tags": ["acme"],
            }
        ],
        "next_cursor": None,
    }
    request = fake.requests[-1]
    assert str(request.url).startswith("https://n8n.acme.example/api/v1/workflows?")
    assert request.url.params["active"] == "true"
    assert "cursor" not in request.url.params  # None params are not sent


async def test_get_workflow(fake: FakeN8n) -> None:
    result = await n8n.get_workflow.handler(target(), workflow_id="wf2")
    assert result["name"] == "Nightly report"
    assert result["nodes"][0]["name"] == "Start"


async def test_executions(fake: FakeN8n) -> None:
    page = await n8n.list_executions.handler(target(), workflow_id="wf1")
    assert page == {
        "executions": [{"id": "ex1", "workflowId": "wf1", "status": "success"}],
        "next_cursor": "page-2",
    }
    detail = await n8n.get_execution.handler(target(), execution_id="ex1", include_data=True)
    assert detail["data"] == {}
    assert fake.requests[-1].url.params["includeData"] == "true"


async def test_activate_and_deactivate(fake: FakeN8n) -> None:
    await n8n.activate_workflow.handler(target(), workflow_id="wf2")
    assert fake.workflows["wf2"]["active"] is True
    assert fake.requests[-1].method == "POST"
    assert fake.requests[-1].url.path == "/api/v1/workflows/wf2/activate"
    await n8n.deactivate_workflow.handler(target(), workflow_id="wf2")
    assert fake.workflows["wf2"]["active"] is False


async def test_update_sends_only_writable_fields_and_never_republishes_by_default(
    fake: FakeN8n,
) -> None:
    result = await n8n.update_workflow.handler(
        target(), workflow_id="wf1", name="Sync leads v2", nodes=[], connections={}
    )
    assert result["name"] == "Sync leads v2"
    request = fake.requests[-1]
    assert request.method == "PUT"
    assert request.url.params["publishIfActive"] == "false"


async def test_update_can_republish_explicitly(fake: FakeN8n) -> None:
    await n8n.update_workflow.handler(
        target(), workflow_id="wf1", name="x", nodes=[], connections={}, republish=True
    )
    assert fake.requests[-1].url.params["publishIfActive"] == "true"


async def test_update_precondition_tracks_updated_at(fake: FakeN8n) -> None:
    precondition = n8n.update_workflow.precondition
    assert precondition is not None
    before = await precondition(target(), workflow_id="wf1", name="x", nodes=[], connections={})
    assert before == {"updatedAt": fake.workflows["wf1"]["updatedAt"]}
    await n8n.activate_workflow.handler(target(), workflow_id="wf1")  # someone edits it
    after = await precondition(target(), workflow_id="wf1")
    assert after != before


async def test_http_errors_are_clear(fake: FakeN8n) -> None:
    with pytest.raises(ToolError, match="n8n returned 404: Not Found"):
        await n8n.get_workflow.handler(target(), workflow_id="missing")


async def test_wrong_key_error_does_not_leak_the_key(fake: FakeN8n) -> None:
    wrong = "n8n-wrong-key-77aa31"
    with pytest.raises(ToolError, match="n8n returned 401: unauthorized") as excinfo:
        await n8n.list_workflows.handler(target(api_key=wrong))
    assert wrong not in scrub.scrub(str(excinfo.value))


async def test_missing_base_url(fake: FakeN8n) -> None:
    with pytest.raises(ToolError, match="has no base_url"):
        await n8n.list_workflows.handler(target(base_url=None))


def test_tools_are_declared_read_or_write() -> None:
    kinds = {t.name: t.kind.value for t in n8n.ADAPTER.tools}
    assert kinds == {
        "list_workflows": "read",
        "get_workflow": "read",
        "list_executions": "read",
        "get_execution": "read",
        "activate_workflow": "write",
        "deactivate_workflow": "write",
        "update_workflow": "write",
    }
