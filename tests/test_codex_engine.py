"""Tasks on the owner's ChatGPT plan, on the backend's side: the gateway's proxy for the plan,
which engine a task runs on, and what Codex's failures mean. The worker's side is in
test_worker_codex.py."""

import json
import time
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient as ApiClient
from starlette.testclient import TestClient

from ringkeeper import claude_failures
from ringkeeper.claude_failures import Kind
from ringkeeper.config import Settings, get_settings
from ringkeeper.gateway import model_proxy
from ringkeeper.models import SessionRole
from ringkeeper.services import chatgpt
from ringkeeper.vault import Vault
from ringkeeper.workers import manager
from ringkeeper.workers.env import worker_env
from tests import test_model_proxy, test_web_api

world, vault, client = test_model_proxy.world, test_model_proxy.vault, test_model_proxy.client
setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner
PLAN_TOKEN = "plan-access-token-0123456789"
RESPONSES_SSE = (
    b'event: response.created\ndata: {"type":"response.created","response":{"model":"gpt-5.5"}}\n\n'
    b'event: response.completed\ndata: {"type":"response.completed","response":{"model":"gpt-5.5",'
    b'"usage":{"input_tokens":40,"output_tokens":9,"input_tokens_details":{"cached_tokens":12}}}}\n\n'
)


def always(_settings: object) -> bool:
    return True


def never(_settings: object) -> bool:
    return False


def signed_in(vault: Vault) -> None:
    vault.store(chatgpt.REF, json.dumps({
        "access_token": PLAN_TOKEN, "refresh_token": "plan-refresh-token-0123456789",
        "id_token": "", "scope": chatgpt.SCOPES, "expires_at": int(time.time()) + 3600,
    }))  # fmt: skip


@pytest.fixture
def plan(monkeypatch: pytest.MonkeyPatch) -> None:
    """As in the desktop app (without its keychain)."""
    monkeypatch.setattr(chatgpt, "available", always)


def test_the_plans_token_goes_on_in_the_gateway_and_the_tasks_stops_there(
    client: TestClient, world: test_model_proxy.Upstreams, vault: Vault, plan: None
) -> None:
    signed_in(vault)
    world.answer = httpx2.Response(
        200, content=RESPONSES_SSE, headers={"content-type": "text/event-stream"}
    )
    sent = test_model_proxy.token()
    found = client.post(
        "/model/chatgpt/v1/responses", json={"model": "gpt-5.5", "stream": True},
        headers={"Authorization": f"Bearer {sent}"},
    )  # fmt: skip
    assert found.status_code == 200
    out = world.seen[0]
    assert str(out.url) == "https://api.openai.com/v1/responses"
    assert out.headers["authorization"] == f"Bearer {PLAN_TOKEN}"
    assert sent not in str(dict(out.headers))


def test_only_a_model_call_is_open_on_the_plan(
    client: TestClient, world: test_model_proxy.Upstreams, vault: Vault, plan: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    signed_in(vault)
    sent = {"Authorization": f"Bearer {test_model_proxy.token()}"}
    assert client.post("/model/chatgpt/v1/files", json={}, headers=sent).status_code == 404
    assert client.get("/model/chatgpt/v1/models", headers=sent).status_code == 200
    monkeypatch.setattr(chatgpt, "available", never)  # a server install
    assert client.post("/model/chatgpt/v1/responses", json={}, headers=sent).status_code == 404


def test_signed_out_says_so(
    client: TestClient, world: test_model_proxy.Upstreams, plan: None
) -> None:
    sent = {"Authorization": f"Bearer {test_model_proxy.token()}"}
    found = client.post("/model/chatgpt/v1/responses", json={}, headers=sent)
    assert found.status_code == 503
    assert "Sign in again" in found.text
    assert not world.seen


def test_the_responses_apis_usage_is_counted() -> None:
    counter = model_proxy.Counter(chatgpt.PROXY_KIND)
    for cut in range(0, len(RESPONSES_SSE), 11):
        counter.feed(RESPONSES_SSE[cut : cut + 11])
    counter.finish()
    assert counter.model == "gpt-5.5"
    assert (counter.tokens["input"], counter.tokens["output"], counter.tokens["cache_read"]) == (
        40, 9, 12,
    )  # fmt: skip


def test_a_codex_worker_holds_no_credential() -> None:
    env = worker_env(chatgpt.worker_vars("http://gateway:8100", "task-model-token"), {"a": 1}, "gw")
    assert env["RINGKEEPER_MODEL_URL"] == "http://gateway:8100/model/chatgpt/v1"
    assert env["RINGKEEPER_MODEL_TOKEN"] == "task-model-token"
    assert env["CODEX_HOME"] == "/state/codex"  # its threads, for resuming, on the task's volume
    assert PLAN_TOKEN not in json.dumps(env)


@pytest.mark.parametrize(
    ("info", "kind"),
    [
        ("usageLimitExceeded", Kind.PLAN_LIMIT),
        ("rateLimitExceeded", Kind.RATE_LIMIT),
        ("serverOverloaded", Kind.OVERLOADED),
        ("unauthorized", Kind.LOGIN),
        ({"responseStreamDisconnected": {"httpStatusCode": 502}}, Kind.NETWORK),
        ("contextWindowExceeded", None),
        (None, None),
    ],
)
def test_codexs_own_failures_mean_what_claudes_do(info: object, kind: Kind | None) -> None:
    found = chatgpt.engine_failure(info)
    assert (found.kind if found else None) is kind
    if kind is Kind.PLAN_LIMIT:
        assert found is not None
        assert "chatgpt.com/settings/usage" in found.message


def test_the_plans_limit_fails_the_task_without_pausing_claude() -> None:
    collected = manager.Collected()
    collected.result = {"is_error": True, "engine": "codex", "engine_error": "usageLimitExceeded"}
    collected.exit_code = 1
    found = manager.claude_failure(collected)
    assert found is not None
    assert found.kind is Kind.PLAN_LIMIT
    assert found.kind is not claude_failures.Kind.USAGE_LIMIT


class NoRuntime:
    """Only where a task runs is asked here: nothing starts."""


def runner(vault: Vault, settings: Settings | None = None) -> manager.Manager:
    return manager.Manager(NoRuntime(), vault, settings or get_settings())  # type: ignore[arg-type]


def job(role: SessionRole = SessionRole.WORKER) -> Any:
    class Job:
        project = "acme-web"

    found = Job()
    found.role = role  # type: ignore[attr-defined]
    return found


def test_a_plan_model_runs_on_codex_and_the_architect_stays_on_claude(
    world: test_model_proxy.Upstreams, vault: Vault
) -> None:
    with pytest.raises(chatgpt.PlanError, match="isn't signed in"):
        runner(vault).where_it_runs(job(), "chatgpt:gpt-5.5", architect_role=False)
    signed_in(vault)
    model, place = runner(vault).where_it_runs(job(), "chatgpt:gpt-5.5", architect_role=False)
    assert (model, place) == ("chatgpt:gpt-5.5", None)
    model, place = runner(vault).where_it_runs(job(), "chatgpt:gpt-5.5", architect_role=True)
    assert place is not None  # on Claude, with its own default model
    assert model is None


def test_task_roles_take_a_plan_model_but_the_architect_doesnt(owner: ApiClient) -> None:
    for role in ("model_worker", "model_low", "model_medium", "model_high"):
        assert owner.patch("/settings", json={role: "chatgpt:gpt-5.5"}).status_code == 200
    assert owner.patch("/settings", json={"model_architect": "chatgpt:gpt-5.5"}).status_code == 422
