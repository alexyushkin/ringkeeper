"""n8n public API (v1): the secret is an API key, base_url is the n8n host.

Endpoints follow n8n's own OpenAPI spec. `activate`/`deactivate` are marked
deprecated in current n8n in favour of `publish`/`unpublish`, but they work
there and are the only option on older self-hosted versions, so we use them.
"""

from typing import Annotated, Any, cast

import httpx2
from pydantic import Field

from interlock.adapters.base import JSON, Adapter, Target, ToolError, guarded_write, read, write
from interlock.models import InstanceType

API_PREFIX = "/api/v1"
TIMEOUT = 30.0
# Fields n8n accepts in PUT /workflows/{id}; everything else is read-only there.
WRITABLE_WORKFLOW_FIELDS = ("name", "nodes", "connections", "settings")
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

WorkflowId = Annotated[str, Field(description="The n8n workflow id.")]
Limit = Annotated[int, Field(ge=1, le=250, description="Page size (n8n caps it at 250).")]
Cursor = Annotated[str | None, Field(description="next_cursor from a previous page.")]


def _client(target: Target) -> httpx2.AsyncClient:
    if not target.base_url:
        raise ToolError(f"instance '{target.name}' has no base_url; set it in the registry")
    return httpx2.AsyncClient(
        base_url=target.base_url.rstrip("/") + API_PREFIX,
        headers={"X-N8N-API-KEY": target.secret.reveal(), "Accept": "application/json"},
        timeout=TIMEOUT,
        transport=transport,
    )


def _error_message(response: httpx2.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return response.reason_phrase or "no details"
    if isinstance(body, dict):
        message = cast(dict[str, object], body).get("message")
        if isinstance(message, str):
            return message
    return response.reason_phrase or "no details"


async def _call(
    target: Target,
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    body: JSON | None = None,
) -> JSON:
    clean_params = {k: v for k, v in (params or {}).items() if v is not None}
    try:
        async with _client(target) as client:
            response = await client.request(method, path, params=clean_params, json=body)
    except httpx2.TimeoutException:
        raise ToolError(f"n8n at instance '{target.name}' timed out") from None
    except httpx2.HTTPError as error:
        raise ToolError(
            f"cannot reach n8n at instance '{target.name}': {type(error).__name__}"
        ) from None
    if response.status_code >= 400:
        raise ToolError(f"n8n returned {response.status_code}: {_error_message(response)}")
    data: Any = response.json() if response.content else {}
    return cast(JSON, data) if isinstance(data, dict) else {"data": data}


def _page(data: JSON, key: str, summarize: Any = None) -> JSON:
    items = cast(list[JSON], data.get("data", []))
    return {
        key: [summarize(i) for i in items] if summarize else items,
        "next_cursor": data.get("nextCursor"),
    }


def _workflow_summary(workflow: JSON) -> JSON:
    tags = cast(list[JSON], workflow.get("tags") or [])
    return {
        "id": workflow.get("id"),
        "name": workflow.get("name"),
        "active": workflow.get("active"),
        "updatedAt": workflow.get("updatedAt"),
        "tags": [t.get("name") for t in tags],
    }


@read
async def list_workflows(
    target: Target,
    active: bool | None = None,
    name: Annotated[str | None, Field(description="Filter by exact name.")] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> JSON:
    """List workflows (id, name, active, updatedAt, tags), one page at a time."""
    params = {
        "active": None if active is None else str(active).lower(),
        "name": name,
        "limit": limit,
        "cursor": cursor,
    }
    return _page(
        await _call(target, "GET", "/workflows", params=params), "workflows", _workflow_summary
    )


@read
async def get_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Get one workflow, including its nodes and connections."""
    return await _call(target, "GET", f"/workflows/{workflow_id}")


@read
async def list_executions(
    target: Target,
    workflow_id: Annotated[str | None, Field(description="Only this workflow's runs.")] = None,
    status: Annotated[
        str | None, Field(description="e.g. success, error, running, waiting, canceled.")
    ] = None,
    limit: Limit = 20,
    cursor: Cursor = None,
) -> JSON:
    """List workflow executions, newest first, one page at a time."""
    params = {"workflowId": workflow_id, "status": status, "limit": limit, "cursor": cursor}
    return _page(await _call(target, "GET", "/executions", params=params), "executions")


@read
async def get_execution(
    target: Target,
    execution_id: Annotated[str, Field(description="The n8n execution id.")],
    include_data: Annotated[bool, Field(description="Include per-node run data.")] = False,
) -> JSON:
    """Get one execution; set include_data for the detailed run data."""
    params = {"includeData": str(include_data).lower()}
    return await _call(target, "GET", f"/executions/{execution_id}", params=params)


@write
async def activate_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Activate (publish) a workflow so its triggers start running."""
    return await _call(target, "POST", f"/workflows/{workflow_id}/activate")


@write
async def deactivate_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Deactivate a workflow so its triggers stop running."""
    return await _call(target, "POST", f"/workflows/{workflow_id}/deactivate")


async def _updated_at(target: Target, workflow_id: str, **_ignored: object) -> JSON:
    """Precondition for update_workflow: approval is refused if this changes."""
    workflow = await _call(target, "GET", f"/workflows/{workflow_id}")
    return {"updatedAt": workflow.get("updatedAt")}


@guarded_write(_updated_at)
async def update_workflow(
    target: Target,
    workflow_id: WorkflowId,
    name: str,
    nodes: list[dict[str, Any]],
    connections: dict[str, Any],
    settings: dict[str, Any] | None = None,
    republish: Annotated[
        bool,
        Field(
            description=(
                "If the workflow is live, also put this version live. Default false: the "
                "update is saved as a draft and the live version keeps running unchanged."
            )
        ),
    ] = False,
) -> JSON:
    """Replace a workflow's name, nodes, connections and settings."""
    body: JSON = {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": settings or {},
    }
    # Never rely on n8n's default (publishIfActive=true): going live must be explicit.
    params = {"publishIfActive": str(republish).lower()}
    return await _call(target, "PUT", f"/workflows/{workflow_id}", params=params, body=body)


ADAPTER = Adapter(
    service=InstanceType.N8N,
    tools=(
        list_workflows,
        get_workflow,
        list_executions,
        get_execution,
        activate_workflow,
        deactivate_workflow,
        update_workflow,
    ),
)
