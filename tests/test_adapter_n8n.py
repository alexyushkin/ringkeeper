import json

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
        "total": 1,
        "active": 1,
    }
    request = fake.requests[-1]
    assert str(request.url).startswith("https://n8n.acme.example/api/v1/workflows?")
    assert request.url.params["active"] == "true"
    assert "cursor" not in request.url.params  # None params are not sent


async def test_the_totals_count_every_page_not_only_this_one(fake: FakeN8n) -> None:
    """The same n8n was once reported with 197 workflows and then 184: the agent counted by
    hand. Each page says how many there are in all, and how many are on."""
    for i in range(3, 8):
        fake.add_workflow(f"wf{i}", f"Workflow {i}", active=i % 2 == 0)
    first = await n8n.list_workflows.handler(target(), limit=3)
    assert len(first["workflows"]) == 3
    assert (first["total"], first["active"]) == (7, 3)
    second = await n8n.list_workflows.handler(target(), limit=3, cursor=first["next_cursor"])
    assert [w["id"] for w in second["workflows"]] == ["wf4", "wf5", "wf6"]
    assert (second["total"], second["active"]) == (7, 3)
    on = await n8n.list_workflows.handler(target(), active=True, limit=2)
    assert (on["total"], on["active"]) == (3, 3)
    assert "counted" not in on


def bayside(fake: FakeN8n) -> None:
    """From a real run: 49 "Bayside #N" and 10 "Mr. Gutter #N" workflows, reported as 46
    and 13 because the worker counted a plain list by hand."""
    for n in range(1, 50):
        fake.add_workflow(f"s{n}", f"Bayside #{n}", tags=("bayside",))
    for n in range(1, 11):
        fake.add_workflow(f"m{n}", f"Mr. Gutter #{n}", tags=("gutter",))


async def test_a_subset_is_counted_by_a_filter_across_every_page(fake: FakeN8n) -> None:
    bayside(fake)
    first = await n8n.list_workflows.handler(target(), name_starts_with="Bayside", limit=20)
    assert (len(first["workflows"]), first["total"]) == (20, 49)
    rest = await n8n.list_workflows.handler(
        target(), name_starts_with="Bayside", limit=20, cursor=first["next_cursor"]
    )
    assert rest["total"] == 49
    assert rest["workflows"][0]["name"] == "Bayside #21"
    gutter = await n8n.list_workflows.handler(target(), name_contains="gutter", limit=5)
    assert gutter["total"] == 10
    tagged = await n8n.list_workflows.handler(target(), tags="gutter, nothing")
    assert tagged["total"] == 10


async def test_a_long_list_comes_with_counts_by_name_and_tag(fake: FakeN8n) -> None:
    bayside(fake)
    page = await n8n.list_workflows.handler(target(), limit=50)
    assert page["total"] == 61  # with the two from the fixture
    assert page["groups"]["by_name"] == {"Bayside": 49, "Mr. Gutter": 10}
    assert page["groups"]["by_tag"]["bayside"] == 49
    assert page["groups"]["by_tag"]["gutter"] == 10


def test_a_name_s_group_is_what_comes_before_a_number_or_a_dash() -> None:
    assert n8n.group_of("Bayside #12") == "Bayside"
    assert n8n.group_of("Mr. Gutter #3") == "Mr. Gutter"
    assert n8n.group_of("Lead sync - Acme") == "Lead sync"
    assert n8n.group_of("Lead sync \u2013 Acme") == "Lead sync"
    assert n8n.group_of("Nightly report") is None


async def test_get_workflow(fake: FakeN8n) -> None:
    result = await n8n.get_workflow.handler(target(), workflow_id="wf2")
    assert result["name"] == "Nightly report"
    assert result["nodes"][0]["name"] == "Start"


async def test_executions(fake: FakeN8n) -> None:
    page = await n8n.list_executions.handler(target(), workflow_id="wf1")
    assert page == {
        "executions": [{"id": "ex1", "workflowId": "wf1", "status": "success"}],
        "next_cursor": None,
        "total": 1,
        "by_status": {"success": 1},
    }
    detail = await n8n.get_execution.handler(target(), execution_id="ex1", include_data=True)
    assert detail["data"] == {}
    assert fake.requests[-1].url.params["includeData"] == "true"


async def test_execution_totals_count_every_page(fake: FakeN8n) -> None:
    """A page of 20 runs isn't how many there are: "how many failed?" is answered by
    by_status, over every page that matches."""
    for i in range(2, 31):
        fake.executions.append(
            {"id": f"ex{i}", "workflowId": "wf1", "status": "error" if i % 3 == 0 else "success"}
        )
    first = await n8n.list_executions.handler(target(), limit=5)
    assert len(first["executions"]) == 5
    assert first["total"] == 30
    assert first["by_status"] == {"error": 10, "success": 20}
    second = await n8n.list_executions.handler(target(), limit=5, cursor=first["next_cursor"])
    assert (second["total"], second["by_status"]) == (30, first["by_status"])
    failed = await n8n.list_executions.handler(target(), status="error", limit=5)
    assert (failed["total"], failed["by_status"]) == (10, {"error": 10})
    assert "counted" not in failed


async def test_execution_totals_stop_at_the_newest_thousand(
    fake: FakeN8n, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(n8n, "EXECUTIONS_COUNTED", 12)
    for i in range(2, 31):
        fake.executions.append({"id": f"ex{i}", "workflowId": "wf1", "status": "success"})
    page = await n8n.list_executions.handler(target(), limit=5)
    assert page["total"] == 12
    assert page["counted"] == "only the newest 12: there are more"


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
        "retry_execution": "write",
        "stop_execution": "write",
    }
    # Every n8n write waits for approval, even on test data; reads never do.
    writes = [t for t in n8n.ADAPTER.tools if t.kind.value == "write"]
    assert {t.hold.value for t in writes} == {"always"}


async def test_retry_and_stop_an_execution(fake: FakeN8n) -> None:
    fake.executions.append({"id": "1", "workflowId": "wf1", "status": "error"})
    retried = await n8n.retry_execution.handler(target(), execution_id="1", latest_version=True)
    assert retried == {"id": "1002", "mode": "retry", "retryOf": "1"}
    assert fake.requests[-1].url.path == "/api/v1/executions/1/retry"
    assert json.loads(fake.requests[-1].content) == {"loadWorkflow": True}
    stopped = await n8n.stop_execution.handler(target(), execution_id="1")
    assert stopped["status"] == "canceled"
    with pytest.raises(ToolError, match="isn't an n8n execution id"):
        await n8n.stop_execution.handler(target(), execution_id="../workflows")
