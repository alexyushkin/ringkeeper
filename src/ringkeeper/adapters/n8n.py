"""n8n public API (v1): the secret is an API key, base_url is the n8n host.

Reads run without asking. Every write waits for approval, even on a
connection marked as test data: turning a workflow on or off, changing it,
and retrying or stopping an execution (n8n's API has no "run this workflow
now"; a retry runs a past execution again).

A link can be narrowed to workflows with one tag, or in one n8n project (an n8n you host
for several clients): then everything is checked here, on every call. A workflow outside it
is refused, an execution counts by its workflow, and lists show only what's inside. n8n
filters lists by tag and project itself, and the answers are checked again here.

Endpoints follow n8n's own OpenAPI spec. `activate`/`deactivate` are marked
deprecated in current n8n in favour of `publish`/`unpublish`, but they work
there and are the only option on older self-hosted versions, so we use them.
"""

import re
from collections import Counter
from dataclasses import replace
from typing import Annotated, Any, cast

import anyio.from_thread
import httpx2
from pydantic import Field

from ringkeeper import netpolicy
from ringkeeper.adapters import undo_services
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Hold,
    Target,
    ToolError,
    always_held,
    guarded_write,
    read,
)
from ringkeeper.models import InstanceType

API_PREFIX = "/api/v1"
TIMEOUT = 30.0
# Fields n8n accepts in PUT /workflows/{id}; everything else is read-only there.
WRITABLE_WORKFLOW_FIELDS = ("name", "nodes", "connections", "settings")
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

# n8n ids are short alphanumeric strings. Anything else (a `/`, `..`, `%`) could
# point a request at a different API path than the tool names, so it's refused.
ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"
_ID = re.compile(ID_PATTERN)
WorkflowId = Annotated[str, Field(description="The n8n workflow id.", pattern=ID_PATTERN)]
ExecutionId = Annotated[str, Field(description="The n8n execution id.", pattern=ID_PATTERN)]


def _id(value: str) -> str:
    if not _ID.fullmatch(value):
        raise ToolError("that isn't an n8n id (letters, digits, - and _ only)")
    return value


Limit = Annotated[int, Field(ge=1, le=250, description="Page size (n8n caps it at 250).")]
Cursor = Annotated[str | None, Field(description="next_cursor from a previous page.")]


def _client(target: Target) -> httpx2.AsyncClient:
    if not target.base_url:
        raise ToolError(f"instance '{target.name}' has no base_url; set it in the registry")
    try:
        netpolicy.check_url(target.base_url)  # the API key only goes to an allowed host
    except netpolicy.AddressNotAllowedError as error:
        raise ToolError(f"instance '{target.name}' can't be used: {error}") from None
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


# --- A link narrowed to one tag or n8n project ---

MAX_SCOPE_PAGES = 40  # 10,000 workflows: more than any one n8n we'd be pointed at


def _scope(target: Target) -> tuple[str, str]:
    return str(target.scope.get("tag") or ""), str(target.scope.get("project") or "")


def _scope_params(target: Target) -> dict[str, str | None]:
    tag, project = _scope(target)
    return {"tags": tag or None, "projectId": project or None}


def _in_scope(target: Target, workflow: JSON) -> bool:
    """Whether a workflow, as n8n listed it, is inside the link's scope. The project is
    also checked by n8n's own filter; where n8n says whose a workflow is, that's checked."""
    tag, project = _scope(target)
    if tag:
        tags = cast(list[JSON], workflow.get("tags") or [])
        if tag.casefold() not in {str(t.get("name") or "").casefold() for t in tags}:
            return False
    shared = cast(list[JSON], workflow.get("shared") or [])
    if project and shared:
        owners = {
            str(s.get("projectId") or cast(JSON, s.get("project") or {}).get("id")) for s in shared
        }
        return project in owners
    return True


def _scope_words(target: Target) -> str:
    tag, project = _scope(target)
    parts = [f"tagged '{tag}'"] if tag else []
    parts += [f"in n8n project {project}"] if project else []
    return " and ".join(parts)


async def _scoped_ids(target: Target) -> set[str]:
    """Every workflow this link may use."""
    found: set[str] = set()
    cursor: str | None = None
    for _ in range(MAX_SCOPE_PAGES):
        params = {**_scope_params(target), "limit": 250, "cursor": cursor}
        data = await _call(target, "GET", "/workflows", params=params)
        for workflow in cast(list[JSON], data.get("data", [])):
            if _in_scope(target, workflow):
                found.add(str(workflow.get("id")))
        cursor = cast(str | None, data.get("nextCursor"))
        if not cursor:
            break
    return found


async def check_workflow(target: Target, workflow_id: str) -> None:
    """Refuses a workflow outside the link's scope (nothing to check without one)."""
    if not target.scope:
        return
    if _id(workflow_id) not in await _scoped_ids(target):
        raise ToolError(
            f"workflow {workflow_id} isn't one this project may use: its link to "
            f"'{target.name}' only reaches workflows {_scope_words(target)}"
        )


async def check_execution(target: Target, execution_id: str) -> None:
    """An execution counts by its workflow."""
    if not target.scope:
        return
    execution = await _call(target, "GET", f"/executions/{_id(execution_id)}")
    workflow = str(execution.get("workflowId") or "")
    if not workflow or workflow not in await _scoped_ids(target):
        raise ToolError(
            f"execution {execution_id} isn't one this project may use: its link to "
            f"'{target.name}' only reaches workflows {_scope_words(target)}"
        )


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


def _counts(workflows: list[JSON], *, more: bool) -> JSON:
    """How many workflows there are and how many are on. `more`: there were more pages than
    were counted."""
    counted: JSON = {
        "total": len(workflows),
        "active": sum(1 for w in workflows if w.get("active")),
    }
    if more:
        counted["counted"] = f"only the first {len(workflows)}: there are more"
    return counted


async def _every_workflow(target: Target, params: JSON) -> tuple[list[JSON], bool]:
    """Every workflow that matches, across all pages; True if there were more pages still."""
    found: list[JSON] = []
    cursor: str | None = None
    for _ in range(MAX_SCOPE_PAGES):
        data = await _call(target, "GET", "/workflows", params={**params, "limit": 250,
                                                                 "cursor": cursor})  # fmt: skip
        found += [w for w in cast(list[JSON], data.get("data", [])) if _in_scope(target, w)]
        cursor = cast(str | None, data.get("nextCursor"))
        if not cursor:
            return found, False
    return found, True


GROUPS_SHOWN = 30
_GROUP_MARKS = ("#", " - ", " \u2013 ", "-")  # \u2013: an en dash


def group_of(name: str) -> str | None:
    """The part of a workflow's name that says whose or what it is: "Seaside #12" is
    Seaside's, "Lead sync - Acme" is a lead sync. None when the name has no such mark."""
    for mark in _GROUP_MARKS:
        head, found, _rest = name.partition(mark)
        if found and head.strip():
            return head.strip()
    return None


def _groups(workflows: list[JSON]) -> JSON:
    """Counts the adapter made, to quote instead of counting a list: by the name's prefix
    (before "#" or the first dash) and by tag."""
    by_name: Counter[str] = Counter()
    by_tag: Counter[str] = Counter()
    for workflow in workflows:
        if (group := group_of(str(workflow.get("name") or ""))) is not None:
            by_name[group] += 1
        for tag in cast(list[JSON], workflow.get("tags") or []):
            by_tag[str(tag.get("name"))] += 1
    # A group of one isn't a group: just a name with a dash in it.
    return {
        "by_name": {g: n for g, n in by_name.most_common(GROUPS_SHOWN) if n > 1},
        "by_tag": dict(by_tag.most_common(GROUPS_SHOWN)),
    }


def _matches(workflow: JSON, contains: str | None, starts: str | None, tags: list[str]) -> bool:
    name = str(workflow.get("name") or "").casefold()
    if contains and contains.casefold() not in name:
        return False
    if starts and not name.startswith(starts.casefold()):
        return False
    has = {str(t.get("name")).casefold() for t in cast(list[JSON], workflow.get("tags") or [])}
    return not tags or any(t.casefold() in has for t in tags)


def _offset(cursor: str | None) -> int:
    """A filtered list's own cursor ("f150"): where the next page starts."""
    return int(cursor[1:]) if cursor and cursor.startswith("f") and cursor[1:].isdigit() else 0


@read
async def list_workflows(
    target: Target,
    active: bool | None = None,
    name: Annotated[str | None, Field(description="Filter by exact name.")] = None,
    name_contains: Annotated[
        str | None, Field(description="Only workflows whose name has these words.", max_length=200)
    ] = None,
    name_starts_with: Annotated[
        str | None, Field(description="Only workflows whose name starts so.", max_length=200)
    ] = None,
    tags: Annotated[
        str | None, Field(description="Comma-separated: workflows with any of these tags.")
    ] = None,
    limit: Limit = 50,
    cursor: Cursor = None,
) -> JSON:
    """List workflows (id, name, active, updatedAt, tags), one page at a time. total and active
    count every workflow that matches the filters, on every page. To answer "how many X",
    filter (name_contains, name_starts_with, tags, active) and quote total: never count a list
    by hand. groups counts them all by the name's prefix (before "#" or the first dash) and by
    tag."""
    params = {
        "active": None if active is None else str(active).lower(),
        "name": name,
        **(_scope_params(target) if target.scope else {}),
    }
    wanted = [t.strip() for t in (tags or "").split(",") if t.strip()]
    if name_contains or name_starts_with or wanted:
        # n8n can't filter like this: every page is read, filtered here, and paged again.
        found, more = await _every_workflow(target, params)
        matching = [w for w in found if _matches(w, name_contains, name_starts_with, wanted)]
        start = _offset(cursor)
        shown = matching[start : start + limit]
        following = f"f{start + limit}" if start + limit < len(matching) else None
        page = (
            {"workflows": [_workflow_summary(w) for w in shown], "next_cursor": following}
            | _counts(matching, more=more)
            | {"groups": _groups(matching)}
        )
        return {**page, "limited_to": _scope_words(target)} if target.scope else page
    data = await _call(target, "GET", "/workflows", params={**params, "limit": limit,
                                                             "cursor": cursor})  # fmt: skip
    # n8n filtered it; nothing outside goes back, whatever it sent.
    kept = [w for w in cast(list[JSON], data.get("data", [])) if _in_scope(target, w)]
    extra: JSON = {}
    if cursor is None and not data.get("nextCursor"):
        counts = _counts(kept, more=False)  # this page is all of them
    else:
        found, more = await _every_workflow(target, params)
        counts = _counts(found, more=more)
        extra = {"groups": _groups(found)}  # a long list: counts to quote, made here
    page = _page({**data, "data": kept}, "workflows", _workflow_summary) | counts | extra
    return {**page, "limited_to": _scope_words(target)} if target.scope else page


@read
async def get_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Get one workflow, including its nodes and connections."""
    await check_workflow(target, workflow_id)
    return await _call(target, "GET", f"/workflows/{_id(workflow_id)}")


EXECUTIONS_COUNTED = 1000  # four pages: n8n keeps thousands of runs, and has no count


def _execution_counts(executions: list[JSON], *, more: bool) -> JSON:
    """How many runs match, and how many of each status. `more`: past what was counted."""
    statuses: dict[str, int] = {}
    for execution in executions:
        status = str(execution.get("status") or "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    counted: JSON = {"total": len(executions), "by_status": dict(sorted(statuses.items()))}
    if more:
        counted["counted"] = f"only the newest {len(executions)}: there are more"
    return counted


async def _every_execution(
    target: Target, params: JSON, allowed: set[str] | None
) -> tuple[list[JSON], bool]:
    """The runs that match, newest first, up to EXECUTIONS_COUNTED; True if there are more."""
    found: list[JSON] = []
    cursor: str | None = None
    while len(found) < EXECUTIONS_COUNTED:
        data = await _call(target, "GET", "/executions", params={**params, "limit": 250,
                                                                  "cursor": cursor})  # fmt: skip
        listed = cast(list[JSON], data.get("data", []))
        found += [e for e in listed if allowed is None or str(e.get("workflowId")) in allowed]
        cursor = cast(str | None, data.get("nextCursor"))
        if not cursor or not listed:
            return found[:EXECUTIONS_COUNTED], len(found) > EXECUTIONS_COUNTED
    return found[:EXECUTIONS_COUNTED], True


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
    """List workflow executions, newest first, one page at a time. total and by_status
    count every run that matches, not only this page (the newest 1,000 at most)."""
    params: JSON = {"workflowId": workflow_id, "status": status}
    allowed: set[str] | None = None
    if target.scope:
        if workflow_id is not None:
            await check_workflow(target, workflow_id)
        allowed = await _scoped_ids(target)
        params["projectId"] = _scope_params(target)["projectId"]
    data = await _call(target, "GET", "/executions", params={**params, "limit": limit,
                                                              "cursor": cursor})  # fmt: skip
    listed = cast(list[JSON], data.get("data", []))
    kept = [e for e in listed if allowed is None or str(e.get("workflowId")) in allowed]
    if cursor is None and not data.get("nextCursor"):
        counts = _execution_counts(kept, more=False)  # this page is all of them
    else:
        found, more = await _every_execution(target, params, allowed)
        counts = _execution_counts(found, more=more)
    page = _page({**data, "data": kept}, "executions") | counts
    return {**page, "limited_to": _scope_words(target)} if target.scope else page


@read
async def get_execution(
    target: Target,
    execution_id: ExecutionId,
    include_data: Annotated[bool, Field(description="Include per-node run data.")] = False,
) -> JSON:
    """Get one execution; set include_data for the detailed run data."""
    await check_execution(target, execution_id)
    params = {"includeData": str(include_data).lower()}
    return await _call(target, "GET", f"/executions/{_id(execution_id)}", params=params)


@always_held
async def activate_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Activate (publish) a workflow so its triggers start running."""
    await check_workflow(target, workflow_id)
    return await _call(target, "POST", f"/workflows/{_id(workflow_id)}/activate")


@always_held
async def deactivate_workflow(target: Target, workflow_id: WorkflowId) -> JSON:
    """Deactivate a workflow so its triggers stop running."""
    await check_workflow(target, workflow_id)
    return await _call(target, "POST", f"/workflows/{_id(workflow_id)}/deactivate")


async def _updated_at(target: Target, workflow_id: str, **_ignored: object) -> JSON:
    """Precondition for update_workflow: approval is refused if this changes."""
    workflow = await _call(target, "GET", f"/workflows/{_id(workflow_id)}")
    return {"updatedAt": workflow.get("updatedAt")}


@guarded_write(_updated_at, Hold.ALWAYS)
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
    await check_workflow(target, workflow_id)
    body: JSON = {
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "settings": settings or {},
    }
    # Never rely on n8n's default (publishIfActive=true): going live must be explicit.
    params = {"publishIfActive": str(republish).lower()}
    return await _call(target, "PUT", f"/workflows/{_id(workflow_id)}", params=params, body=body)


NumericExecutionId = Annotated[
    str, Field(description="The n8n execution id (a number).", pattern=r"^[0-9]{1,18}$")
]


def _execution(value: str) -> str:
    if not value.isdigit():
        raise ToolError("that isn't an n8n execution id (a number)")
    return value


@always_held
async def retry_execution(
    target: Target,
    execution_id: NumericExecutionId,
    latest_version: Annotated[
        bool,
        Field(description="Run the workflow as it's saved now, not as it was for that run."),
    ] = False,
) -> JSON:
    """Run a past execution again (a new execution; the old one stays as it was)."""
    await check_execution(target, execution_id)
    path = f"/executions/{_execution(execution_id)}/retry"
    return await _call(target, "POST", path, body={"loadWorkflow": latest_version})


@always_held
async def stop_execution(target: Target, execution_id: NumericExecutionId) -> JSON:
    """Stop an execution that's running or waiting."""
    await check_execution(target, execution_id)
    return await _call(target, "POST", f"/executions/{_execution(execution_id)}/stop")


def _screen(check: Any, key: str) -> Any:
    """Before the call: outside the link's scope it's refused and recorded as refused, and a
    write isn't held for an approval that would only refuse it (it checks again when it runs)."""

    def screen(target: Target, args: JSON) -> JSON:
        if target.scope and args.get(key):  # list_executions: only if it names one
            anyio.from_thread.run(check, target, str(args[key]))
        return args

    return screen


# What each change replaced is kept for 7 days, to put back (services.undo).
_by_workflow = _screen(check_workflow, "workflow_id")
_by_execution = _screen(check_execution, "execution_id")
update_workflow = replace(
    update_workflow, undo=undo_services.N8nWorkflowUndo(), screen=_by_workflow
)
activate_workflow = replace(
    activate_workflow, undo=undo_services.N8nActiveUndo(), screen=_by_workflow
)
deactivate_workflow = replace(
    deactivate_workflow, undo=undo_services.N8nActiveUndo(), screen=_by_workflow
)
get_workflow = replace(get_workflow, screen=_by_workflow)
get_execution = replace(get_execution, screen=_by_execution)
list_executions = replace(list_executions, screen=_by_workflow)
retry_execution = replace(retry_execution, screen=_by_execution)
stop_execution = replace(stop_execution, screen=_by_execution)

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
        retry_execution,
        stop_execution,
    ),
)
