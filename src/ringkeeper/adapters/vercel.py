"""Vercel REST API: the secret is an access token (vercel.com/account/tokens).

Reads cover projects, deployments and their build logs, and env variable
*names* (values never leave Vercel through here). The only writes put an
existing build live again: redeploy, promote, roll back. Each is held on
production connections like any write. No env changes, domains or purchases.

A token may reach several teams; every tool takes an optional team_id
(from list_teams), which Vercel needs for a team's resources.
"""

import re
from typing import Annotated, Any, cast
from urllib.parse import quote

import httpx2
from pydantic import Field

from ringkeeper.adapters import rest
from ringkeeper.adapters.base import JSON, Adapter, Target, ToolError, read, write
from ringkeeper.models import InstanceType

API = "https://api.vercel.com"
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$"
TeamId = Annotated[
    str | None, Field(description="The team's id (team_...), from list_teams.", pattern=ID_PATTERN)
]
Project = Annotated[str, Field(description="The project's id or name.", pattern=ID_PATTERN)]
DeploymentId = Annotated[
    str, Field(description="The deployment's id (dpl_...) or URL host.", pattern=ID_PATTERN)
]
Limit = Annotated[int, Field(ge=1, le=100)]


def _client(target: Target) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=API,
        headers={"Authorization": f"Bearer {target.secret.reveal()}"},
        timeout=rest.TIMEOUT,
        transport=transport,
    )


def _id(value: str) -> str:
    if not re.fullmatch(ID_PATTERN, value):
        raise ToolError("that isn't a Vercel id or name (letters, digits, _ . -)")
    return quote(value, safe="")


async def _call(
    target: Target, method: str, path: str, team_id: str | None = None, **kwargs: Any
) -> JSON:
    params = {**kwargs.pop("params", {}), "teamId": team_id and _id(team_id)}
    return await rest.call(_client(target), "Vercel", method, path, params=params, **kwargs)


async def whoami(target: Target) -> JSON:
    """The token's user (the Test button)."""
    return await _call(target, "GET", "/v2/user")


@read
async def list_teams(target: Target) -> JSON:
    """List the teams this token can reach (id, slug, name)."""
    data = await _call(target, "GET", "/v2/teams")
    teams = cast(list[JSON], data.get("teams", []))
    return {
        "teams": [{"id": t.get("id"), "slug": t.get("slug"), "name": t.get("name")} for t in teams]
    }


@read
async def list_projects(
    target: Target,
    team_id: TeamId = None,
    search: Annotated[str | None, Field(description="Part of a project name.")] = None,
    limit: Limit = 20,
) -> JSON:
    """List projects (id, name, framework, latest production URL)."""
    data = await _call(
        target, "GET", "/v10/projects", team_id, params={"search": search, "limit": limit}
    )
    projects = cast(list[JSON], data.get("projects", []))
    return {
        "projects": [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "framework": p.get("framework"),
                "updatedAt": p.get("updatedAt"),
            }
            for p in projects
        ],
        "pagination": data.get("pagination"),
    }


@read
async def list_deployments(
    target: Target,
    project: Project,
    team_id: TeamId = None,
    environment: Annotated[
        str | None, Field(description="production or preview.", pattern=r"^(production|preview)$")
    ] = None,
    limit: Limit = 10,
) -> JSON:
    """List a project's deployments, newest first (id, url, state, environment, created)."""
    params = {"projectId": _id(project), "target": environment, "limit": limit}
    data = await _call(target, "GET", "/v6/deployments", team_id, params=params)
    deployments = cast(list[JSON], data.get("deployments", []))
    return {
        "deployments": [
            {
                "id": d.get("uid"),
                "url": d.get("url"),
                "state": d.get("state") or d.get("readyState"),
                "environment": d.get("target") or "preview",
                "created": d.get("created"),
                "commit": cast(JSON, d.get("meta") or {}).get("githubCommitMessage"),
            }
            for d in deployments
        ]
    }


@read
async def get_deployment(
    target: Target, deployment_id: DeploymentId, team_id: TeamId = None
) -> JSON:
    """Get one deployment: state, URL, environment, errors if it failed."""
    data = await _call(target, "GET", f"/v13/deployments/{_id(deployment_id)}", team_id)
    keys = ("id", "name", "url", "readyState", "target", "createdAt", "errorCode", "errorMessage")
    return {key: data.get(key) for key in keys} | {"aliases": data.get("alias", [])}


@read
async def get_build_logs(
    target: Target,
    deployment_id: DeploymentId,
    team_id: TeamId = None,
    limit: Annotated[int, Field(ge=1, le=2000)] = 300,
) -> JSON:
    """The deployment's build output, the last `limit` lines."""
    params = {"builds": 1, "limit": limit, "direction": "backward"}
    data = await _call(
        target, "GET", f"/v3/deployments/{_id(deployment_id)}/events", team_id, params=params
    )
    events = cast(list[JSON], data.get("data", []))
    lines = [
        str(cast(JSON, e.get("payload") or {}).get("text") or e.get("text") or "") for e in events
    ]
    return {"lines": [line for line in lines if line][-limit:]}


@read
async def list_env_names(target: Target, project: Project, team_id: TeamId = None) -> JSON:
    """List a project's env variable names and where they apply. Values are never shown."""
    data = await _call(target, "GET", f"/v10/projects/{_id(project)}/env", team_id)
    variables = cast(list[JSON], data.get("envs", []))
    return {
        "variables": [
            {"key": v.get("key"), "environments": v.get("target"), "type": v.get("type")}
            for v in variables
        ]
    }


@write
async def redeploy(
    target: Target,
    deployment_id: DeploymentId,
    project: Annotated[
        str, Field(description="The deployment's project name.", pattern=ID_PATTERN)
    ],
    team_id: TeamId = None,
    to_production: Annotated[
        bool, Field(description="Make the new deployment the production one.")
    ] = False,
) -> JSON:
    """Build an existing deployment again (same code and settings) as a new deployment."""
    body: JSON = {"name": project, "deploymentId": deployment_id}
    if to_production:
        body["target"] = "production"
    data = await _call(target, "POST", "/v13/deployments", team_id, json=body)
    return {key: data.get(key) for key in ("id", "url", "readyState", "target")}


@write
async def promote(
    target: Target, project: Project, deployment_id: DeploymentId, team_id: TeamId = None
) -> JSON:
    """Make an existing deployment the project's production deployment (no rebuild)."""
    path = f"/v10/projects/{_id(project)}/promote/{_id(deployment_id)}"
    await _call(target, "POST", path, team_id)
    return {"promoted": deployment_id}


@write
async def rollback(
    target: Target, project: Project, deployment_id: DeploymentId, team_id: TeamId = None
) -> JSON:
    """Put an earlier production deployment back live (instant rollback)."""
    path = f"/v9/projects/{_id(project)}/rollback/{_id(deployment_id)}"
    await _call(target, "POST", path, team_id)
    return {"rolled_back_to": deployment_id}


ADAPTER = Adapter(
    service=InstanceType.VERCEL,
    tools=(
        list_teams,
        list_projects,
        list_deployments,
        get_deployment,
        get_build_logs,
        list_env_names,
        redeploy,
        promote,
        rollback,
    ),
)
