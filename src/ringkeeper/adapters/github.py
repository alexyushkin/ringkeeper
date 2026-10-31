"""GitHub through its REST API with a token: a fine-grained personal access token
(limited to the repositories it may reach), or a classic one where an
organization doesn't allow fine-grained tokens yet. The connection's address is
the GitHub host: empty for github.com, or a GitHub Enterprise Server address
(https://github.acme.example), whose API is at /api/v3.

Every repository tool takes `repo` ("owner/name"). A project's link can pin one
repository (ringkeeper.remote NATIVE): a call naming another is refused, one
leaving it out gets it filled in, and the tools that work across the whole
account (list_repos) aren't available on a pinned link.

Reading runs. Creating issues and pull requests, comments, changes to issues,
merges and pushes always wait for approval; nothing here touches a repository's
or the account's settings. A merge needs the connection's name typed to approve.

push_branch pushes one of the project's task branches (ringkeeper/<task>) from
the project's folder with this connection's token, in git's environment (never
on its command line), with hooks off and every credential helper cleared: the
computer's own git credentials are never used.
"""

import base64
import os
import re
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote, urlsplit

import anyio.to_thread
import httpx2
from pydantic import Field

from ringkeeper.adapters import rest
from ringkeeper.adapters.base import (
    JSON,
    Adapter,
    Target,
    ToolError,
    ToolKind,
    always_held,
    read,
)
from ringkeeper.models import InstanceType

PUBLIC_API = "https://api.github.com"
PUBLIC_WEB = "https://github.com"
BRANCH_PREFIX = "ringkeeper/"
MAX_TEXT_CHARS = 20_000
GIT_TIMEOUT = 300
# Tests swap in httpx2.MockTransport; production uses the real network.
transport: httpx2.AsyncBaseTransport | None = None

Repo = Annotated[
    str,
    Field(
        description='The repository as "owner/name", like acme-inc/website.',
        pattern=r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$",
    ),
]
Number = Annotated[int, Field(ge=1, description="The issue or pull request number.")]
Limit = Annotated[int, Field(ge=1, le=100)]
Text = Annotated[str, Field(max_length=65_000)]
Branch = Annotated[str, Field(min_length=1, max_length=250, pattern=r"^[A-Za-z0-9._/-]+$")]


def web_url(target: Target) -> str:
    """The GitHub host the people use: github.com, or the Enterprise Server's address."""
    return (target.base_url or PUBLIC_WEB).rstrip("/")


def api_url(target: Target) -> str:
    host = web_url(target)
    return PUBLIC_API if host == PUBLIC_WEB else f"{host}/api/v3"


def token(target: Target) -> str:
    value = "".join(target.secret.reveal().split())
    if not value:
        raise ToolError("this GitHub connection has no token")
    return value


def secret_fragments(value: str) -> list[str]:
    value = "".join(value.split())
    if not value:
        return []
    basic = base64.b64encode(f"x-access-token:{value}".encode()).decode()
    return [value, basic]


def _client(target: Target) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        base_url=api_url(target),
        headers={
            "Authorization": f"Bearer {token(target)}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=rest.TIMEOUT,
        transport=transport,
    )


async def _call(target: Target, method: str, path: str, **kwargs: Any) -> JSON:
    return await rest.call(_client(target), "GitHub", method, path, **kwargs)


def _repo(repo: str) -> str:
    owner, name = repo.split("/", 1)
    return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"


async def whoami(target: Target) -> JSON:
    return await _call(target, "GET", "/user")


async def probe(target: Target) -> str:
    """Who the token is (the Test button)."""
    me = await whoami(target)
    where = "" if web_url(target) == PUBLIC_WEB else f" on {urlsplit(web_url(target)).netloc}"
    return f"Connected to GitHub{where} as {me.get('login')}."


def _issue(item: JSON) -> JSON:
    return {
        "number": item.get("number"),
        "title": item.get("title"),
        "state": item.get("state"),
        "author": cast(JSON, item.get("user") or {}).get("login"),
        "labels": [label.get("name") for label in cast(list[JSON], item.get("labels") or [])],
        "updated": item.get("updated_at"),
        "url": item.get("html_url"),
    }


# --- Reads ---


@read
async def list_repos(target: Target, limit: Limit = 30) -> JSON:
    """The repositories this token can reach (not available on a link pinned to one)."""
    found = await _call(target, "GET", "/user/repos", params={"per_page": limit, "sort": "updated"})
    repos = cast(list[JSON], found.get("data") or [])
    return {"repos": [{"repo": r.get("full_name"), "private": r.get("private")} for r in repos]}


@read
async def get_repo(target: Target, repo: Repo) -> JSON:
    """A repository: description, default branch, visibility, open issues."""
    r = await _call(target, "GET", _repo(repo))
    return {
        k: r.get(k)
        for k in ("full_name", "description", "default_branch", "private", "open_issues_count")
    } | {"url": r.get("html_url")}


@read
async def list_issues(
    target: Target,
    repo: Repo,
    state: Literal["open", "closed", "all"] = "open",
    labels: Annotated[str, Field(max_length=500, description="Comma-separated.")] = "",
    limit: Limit = 30,
) -> JSON:
    """Issues in a repository, newest first (pull requests left out)."""
    found = await _call(
        target, "GET", f"{_repo(repo)}/issues",
        params={"state": state, "labels": labels or None, "per_page": limit},
    )  # fmt: skip
    items = cast(list[JSON], found.get("data") or [])
    return {"issues": [_issue(i) for i in items if "pull_request" not in i]}


@read
async def get_issue(target: Target, repo: Repo, number: Number) -> JSON:
    """One issue or pull request's conversation: its text and its comments."""
    issue = await _call(target, "GET", f"{_repo(repo)}/issues/{number}")
    comments = await _call(target, "GET", f"{_repo(repo)}/issues/{number}/comments")
    body = str(issue.get("body") or "")
    return _issue(issue) | {
        "body": body[:MAX_TEXT_CHARS],
        "comments": [
            {"author": cast(JSON, c.get("user") or {}).get("login"), "body": c.get("body")}
            for c in cast(list[JSON], comments.get("data") or [])
        ],
    }


@read
async def list_pulls(
    target: Target, repo: Repo, state: Literal["open", "closed", "all"] = "open", limit: Limit = 30
) -> JSON:
    """Pull requests in a repository (number, title, branch, author)."""
    found = await _call(
        target, "GET", f"{_repo(repo)}/pulls", params={"state": state, "per_page": limit}
    )
    return {
        "pulls": [
            _issue(p) | {"head": cast(JSON, p.get("head") or {}).get("ref")}
            for p in cast(list[JSON], found.get("data") or [])
        ]
    }


@read
async def get_pull(target: Target, repo: Repo, number: Number) -> JSON:
    """A pull request: its branches, whether it can merge, and the files it changes."""
    pull = await _call(target, "GET", f"{_repo(repo)}/pulls/{number}")
    files = await _call(target, "GET", f"{_repo(repo)}/pulls/{number}/files")
    return _issue(pull) | {
        "head": cast(JSON, pull.get("head") or {}).get("ref"),
        "base": cast(JSON, pull.get("base") or {}).get("ref"),
        "mergeable": pull.get("mergeable"),
        "body": str(pull.get("body") or "")[:MAX_TEXT_CHARS],
        "files": [
            {"file": f.get("filename"), "status": f.get("status"), "changes": f.get("changes")}
            for f in cast(list[JSON], files.get("data") or [])
        ],
    }


@read
async def get_file(
    target: Target,
    repo: Repo,
    path: Annotated[str, Field(min_length=1, max_length=1000)],
    ref: Annotated[
        str | None, Field(max_length=250, description="A branch, tag or commit.")
    ] = None,
) -> JSON:
    """A file's text from a repository (a folder: its entries)."""
    found = await _call(
        target, "GET", f"{_repo(repo)}/contents/{quote(path.strip('/'))}", params={"ref": ref}
    )
    if "data" in found:  # a folder
        entries = cast(list[JSON], found["data"])
        return {
            "folder": path,
            "entries": [{"name": e.get("name"), "type": e.get("type")} for e in entries],
        }
    content = base64.b64decode(str(found.get("content") or "")).decode("utf-8", "replace")
    return {
        "path": path,
        "text": content[:MAX_TEXT_CHARS],
        "truncated": len(content) > MAX_TEXT_CHARS,
    }


@read
async def list_branches(target: Target, repo: Repo, limit: Limit = 50) -> JSON:
    """A repository's branches."""
    found = await _call(target, "GET", f"{_repo(repo)}/branches", params={"per_page": limit})
    return {"branches": [b.get("name") for b in cast(list[JSON], found.get("data") or [])]}


@read
async def list_commits(
    target: Target, repo: Repo, branch: Branch | None = None, limit: Limit = 20
) -> JSON:
    """Recent commits on a branch (the default one if left out)."""
    found = await _call(
        target, "GET", f"{_repo(repo)}/commits", params={"sha": branch, "per_page": limit}
    )
    return {
        "commits": [
            {
                "sha": str(c.get("sha") or "")[:12],
                "message": str(cast(JSON, c.get("commit") or {}).get("message") or "")[:300],
            }
            for c in cast(list[JSON], found.get("data") or [])
        ]
    }


# --- Writes: each waits for approval ---


@always_held
async def create_issue(
    target: Target,
    repo: Repo,
    title: Annotated[str, Field(min_length=1, max_length=256)],
    body: Text = "",
    labels: list[str] | None = None,
) -> JSON:
    """Open an issue."""
    made = await _call(
        target, "POST", f"{_repo(repo)}/issues",
        json={"title": title, "body": body, **({"labels": labels} if labels else {})},
    )  # fmt: skip
    return {"created": made.get("number"), "url": made.get("html_url")}


@always_held
async def comment(target: Target, repo: Repo, number: Number, body: Text) -> JSON:
    """Comment on an issue or a pull request."""
    made = await _call(
        target, "POST", f"{_repo(repo)}/issues/{number}/comments", json={"body": body}
    )
    return {"commented": number, "url": made.get("html_url")}


@always_held
async def update_issue(
    target: Target,
    repo: Repo,
    number: Number,
    state: Literal["open", "closed"] | None = None,
    title: Annotated[str | None, Field(max_length=256)] = None,
    body: Text | None = None,
    labels: list[str] | None = None,
) -> JSON:
    """Change an issue or pull request: close or reopen it, its title, text or labels."""
    changes = {
        k: v for k, v in {"state": state, "title": title, "body": body, "labels": labels}.items()
        if v is not None
    }  # fmt: skip
    if not changes:
        raise ToolError("nothing to change")
    done = await _call(target, "PATCH", f"{_repo(repo)}/issues/{number}", json=changes)
    return {"updated": number, "state": done.get("state")}


@always_held
async def create_pull(
    target: Target,
    repo: Repo,
    head: Branch,
    title: Annotated[str, Field(min_length=1, max_length=256)],
    base: Branch | None = None,
    body: Text = "",
    draft: bool = False,
) -> JSON:
    """Open a pull request from a branch (push it first with push_branch) into base (the
    default branch if left out)."""
    if base is None:
        base = str((await _call(target, "GET", _repo(repo))).get("default_branch") or "main")
    made = await _call(
        target, "POST", f"{_repo(repo)}/pulls",
        json={"title": title, "head": head, "base": base, "body": body, "draft": draft},
    )  # fmt: skip
    return {"created": made.get("number"), "url": made.get("html_url")}


@always_held
async def merge_pull(
    target: Target,
    repo: Repo,
    number: Number,
    method: Literal["merge", "squash", "rebase"] = "squash",
) -> JSON:
    """Merge a pull request. Waits for approval with the connection's name typed."""
    done = await _call(
        target, "PUT", f"{_repo(repo)}/pulls/{number}/merge", json={"merge_method": method}
    )
    return {"merged": bool(done.get("merged")), "sha": done.get("sha")}


def run_git(args: list[str], cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """One git command (tests swap this for a fake)."""
    return subprocess.run(  # noqa: S603 (fixed argv, no shell)
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=False,  # noqa: S607
        timeout=GIT_TIMEOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )  # fmt: skip


def pushes_here(mode: str) -> bool:
    """The project folders are on this computer (the desktop app); a server's are in its
    workers' containers."""
    return mode == "desktop"


def project_folder(project: str | None) -> str:
    """The calling project's folder, on this computer."""
    from ringkeeper.config import get_settings  # noqa: PLC0415 (no cycle at import)
    from ringkeeper.db import session_scope  # noqa: PLC0415
    from ringkeeper.services import projects  # noqa: PLC0415

    if not pushes_here(get_settings().mode):
        raise ToolError("pushing from the server app isn't available yet; the desktop app can")
    if project is None:
        raise ToolError("no project called this")
    with session_scope(commit=False) as session:
        path = projects.get_project(session, project).workspace_path
    if not path or not Path(path).is_absolute() or not (Path(path) / ".git").exists():
        raise ToolError(f"'{project}''s folder isn't a git repository")
    return path


def git_env(secret: str) -> dict[str, str]:
    """git with this token and nothing of the computer's: no credential helper, no prompt."""
    basic = base64.b64encode(f"x-access-token:{secret}".encode()).decode()
    keep = ("PATH", "SYSTEMROOT", "TEMP", "TMP", "LANG", "LC_ALL", "HOME", "USERPROFILE")
    return {k: os.environ[k] for k in keep if k in os.environ} | {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "never",
        "GIT_ASKPASS": "",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",  # clears every helper: the computer's credentials never apply
        "GIT_CONFIG_KEY_1": "http.extraHeader",
        "GIT_CONFIG_VALUE_1": f"Authorization: Basic {basic}",
        "GIT_CONFIG_KEY_2": "core.hooksPath",
        "GIT_CONFIG_VALUE_2": os.devnull,  # a task's files never run on push
    }


@always_held
async def push_branch(target: Target, repo: Repo, branch: Branch) -> JSON:
    """Push one of this project's task branches (ringkeeper/<task>) from the project's folder
    to the repository, with this connection's token. Then open a pull request with
    create_pull. Never forces, and only ringkeeper/ branches."""
    if not branch.startswith(BRANCH_PREFIX) or ".." in branch:
        raise ToolError(f"only task branches can be pushed ({BRANCH_PREFIX}...), not '{branch}'")
    secret = token(target)
    folder = await anyio.to_thread.run_sync(project_folder, target.project)
    env = git_env(secret)
    remote = f"{web_url(target)}/{repo}.git"
    ref = f"refs/heads/{branch}"

    def run() -> subprocess.CompletedProcess[str]:
        found = run_git(["rev-parse", "--verify", "--quiet", ref], folder, env)
        if found.returncode != 0:
            raise ToolError(f"there's no branch '{branch}' in the project's folder")
        return run_git(["push", "--no-verify", "--porcelain", remote, f"{ref}:{ref}"], folder, env)

    done = await anyio.to_thread.run_sync(run)
    if done.returncode != 0:
        said = re.sub(r"\s+", " ", (done.stderr or done.stdout))[-400:]
        raise ToolError(f"git push didn't work: {said}")
    return {"pushed": branch, "to": f"{web_url(target)}/{repo}/tree/{branch}"}


def _task_branch(_target: Target, args: JSON) -> ToolKind:
    """Before a push is held: only a task branch, so nobody approves one that can't run."""
    branch = str(args.get("branch") or "")
    if not branch.startswith(BRANCH_PREFIX) or ".." in branch:
        raise ToolError(f"only task branches can be pushed ({BRANCH_PREFIX}...), not '{branch}'")
    return ToolKind.WRITE


push_branch = replace(push_branch, classify=_task_branch)

TOOLS = (
    list_repos, get_repo, list_issues, get_issue, list_pulls, get_pull, get_file,
    list_branches, list_commits,
    create_issue, comment, update_issue, create_pull, merge_pull, push_branch,
)  # fmt: skip
ADAPTER = Adapter(service=InstanceType.GITHUB, tools=TOOLS, secret_fragments=secret_fragments)
WRITES = tuple(t.name for t in TOOLS if t.kind is ToolKind.WRITE)
