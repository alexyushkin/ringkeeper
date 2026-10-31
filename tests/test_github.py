"""GitHub connections (a fake GitHub): tokens per client and your own, a repository
pinned per project, account-wide tools refused on a pinned link, writes held, a
GitHub Enterprise Server address, and pushes that use the calling project's own
token from its own folder (never the computer's git credentials)."""

import asyncio
import base64
import subprocess
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ringkeeper.adapters import github
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import InstanceType
from ringkeeper.services import bindings, instances, secrets_in
from ringkeeper.services.calls import RefusalError, approve, run_call
from ringkeeper.vault import get_vault
from tests.helpers import bind, make_instance, make_project

TOKENS = {
    "acme-gh": "github_pat_acme0000000000",
    "globex-gh": "ghp_globex000000000000",
    "mine-gh": "ghp_mine00000000000000",
    "acme-ghes": "github_pat_acmeghes000000",
}


class FakeGitHub:
    def __init__(self) -> None:
        self.seen: list[tuple[str, str, str]] = []  # (token, method, url)

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        token = request.headers["Authorization"].removeprefix("Bearer ")
        self.seen.append((token, request.method, str(request.url)))
        path = request.url.path.removeprefix("/api/v3")
        if path == "/user":
            return httpx2.Response(200, json={"login": "who"})
        if path.endswith("/issues") and request.method == "GET":
            repo = "/".join(path.split("/")[2:4])
            return httpx2.Response(200, json=[{"number": 1, "title": f"in {repo}", "labels": []}])
        if path == "/user/repos":
            return httpx2.Response(200, json=[{"full_name": "acme-inc/website"}])
        return httpx2.Response(404, json={"message": f"no {path}"})


class FakeGit:
    """Records each git command: which folder, and which token it carried."""

    def __init__(self) -> None:
        self.runs: list[tuple[list[str], str, str]] = []  # (args, folder, token)

    def __call__(
        self, args: list[str], cwd: str, env: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        header = env["GIT_CONFIG_VALUE_1"].removeprefix("Authorization: Basic ")
        used = base64.b64decode(header).decode().removeprefix("x-access-token:")
        assert env["GIT_CONFIG_KEY_0"] == "credential.helper"
        assert env["GIT_CONFIG_VALUE_0"] == ""  # the computer's helpers cleared
        assert env["GIT_TERMINAL_PROMPT"] == "0"
        assert used not in " ".join(args)  # never on the command line
        self.runs.append((args, cwd, used))
        return subprocess.CompletedProcess(args, 0, "", "")


def anywhere(_mode: str) -> bool:
    return True


@pytest.fixture
def world(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[FakeGitHub, FakeGit, dict[str, Path]]:
    fake, git = FakeGitHub(), FakeGit()
    monkeypatch.setattr(github, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setattr(github, "run_git", git)
    monkeypatch.setattr(github, "pushes_here", anywhere)
    folders: dict[str, Path] = {}
    with session_scope() as session:
        for project, client in (("acme-web", "acme"), ("globex-app", "globex")):
            folder = tmp_path / project
            (folder / ".git").mkdir(parents=True)
            folders[project] = folder
            make_project(session, project, owner=client, workspace_path=str(folder))
        for name, owner, shared, base in (
            ("acme-gh", "acme", False, None),
            ("globex-gh", "globex", False, None),
            ("mine-gh", instances.SHARED_OWNER, True, None),
            ("acme-ghes", "acme", False, "https://github.acme.example"),
        ):
            ref = f"file://ringkeeper/connections/{name}"
            get_vault().store(
                ref, secrets_in.check_connection_secret(InstanceType.GITHUB, TOKENS[name])
            )
            make_instance(
                session, name, InstanceType.GITHUB, secret_ref=ref, owner=owner, shared=shared,
                base_url=base,
            )  # fmt: skip
    bind("acme-web", "acme-gh", scope={"repo": "acme-inc/website"}, is_default=True)
    bind("globex-app", "globex-gh", scope={"repo": "globex/app"})
    bind("acme-web", "mine-gh")
    bind("globex-app", "mine-gh")
    bind("acme-web", "acme-ghes", scope={"repo": "acme-inc/internal"})
    return fake, git, folders


def call(project: str, tool: str, instance: str | None = None, **args: Any) -> Any:
    caller = Caller(project, "session-1", "token-1")
    return asyncio.run(
        run_call(ToolCall(caller, InstanceType.GITHUB, tool, instance, args), get_vault())
    )


def test_two_projects_two_tokens_never_cross(
    world: tuple[FakeGitHub, FakeGit, dict[str, Path]],
) -> None:
    fake, _, _ = world
    # The pinned repository is filled in, with the project's own token.
    assert call("acme-web", "list_issues")["issues"][0]["title"] == "in acme-inc/website"
    assert fake.seen[-1][0] == TOKENS["acme-gh"]
    assert call("globex-app", "list_issues", "globex-gh")["issues"][0]["title"] == "in globex/app"
    assert fake.seen[-1][0] == TOKENS["globex-gh"]
    # Another repository, another client's connection, or an account-wide tool: refused.
    with pytest.raises(RefusalError, match="limited to repository 'acme-inc/website'"):
        call("acme-web", "list_issues", "acme-gh", repo="globex/app")
    with pytest.raises(RefusalError, match="not available to project 'acme-web'"):
        call("acme-web", "list_issues", "globex-gh", repo="globex/app")
    with pytest.raises(RefusalError, match="not available to project 'globex-app'"):
        call("globex-app", "list_issues", "acme-gh")
    with pytest.raises(RefusalError, match="whole GitHub account"):
        call("acme-web", "list_repos", "acme-gh")
    with session_scope() as session, pytest.raises(RuleViolationError, match="one client's"):
        bindings.check_same_client(session, "globex-app", "acme-gh")

    # Your own token works in both clients' projects (unpinned: any repo it can reach).
    calls_before = len(fake.seen)
    call("acme-web", "list_issues", "mine-gh", repo="acme-inc/website")
    call("globex-app", "list_issues", "mine-gh", repo="globex/app")
    assert [s[0] for s in fake.seen[calls_before:]] == [TOKENS["mine-gh"]] * 2
    assert call("acme-web", "list_repos", "mine-gh")["repos"][0]["repo"] == "acme-inc/website"

    # GitHub Enterprise Server: its own API address.
    call("acme-web", "list_issues", "acme-ghes")
    assert fake.seen[-1][2].startswith(
        "https://github.acme.example/api/v3/repos/acme-inc/internal/"
    )


def test_writes_wait_and_a_push_uses_the_projects_token(
    world: tuple[FakeGitHub, FakeGit, dict[str, Path]],
) -> None:
    fake, git, folders = world
    held = call("acme-web", "create_issue", "acme-gh", title="Broken link")
    assert held["status"] == "held_for_approval"
    assert all(method == "GET" for _, method, _ in fake.seen)  # nothing was created

    with pytest.raises(Exception, match="only task branches"):
        call("acme-web", "push_branch", "acme-gh", branch="main")
    pushes: dict[tuple[str, str], str] = {}
    for project, instance in (("acme-web", "acme-gh"), ("globex-app", "globex-gh"),
                              ("globex-app", "mine-gh")):  # fmt: skip
        repo = "globex/app" if project == "globex-app" else "acme-inc/website"
        args = {"branch": "ringkeeper/task-1"} | ({"repo": repo} if instance == "mine-gh" else {})
        result = call(project, "push_branch", instance, **args)
        assert result["status"] == "held_for_approval"  # a push always asks first
        pushes[(project, instance)] = result["decision_id"]
    assert git.runs == []

    for (project, instance), decision in pushes.items():
        asyncio.run(approve(decision, by="owner", vault=get_vault()))
        args, folder, used = git.runs[-1]
        assert folder == str(folders[project])  # the calling project's own folder
        assert used == TOKENS[instance]  # with that connection's token
        repo = "globex/app" if project == "globex-app" else "acme-inc/website"
        assert args[:3] == ["push", "--no-verify", "--porcelain"]
        assert args[3] == f"https://github.com/{repo}.git"
        assert args[4] == "refs/heads/ringkeeper/task-1:refs/heads/ringkeeper/task-1"


def test_a_token_is_checked_before_its_stored() -> None:
    with pytest.raises(RuleViolationError, match="github_pat_"):
        secrets_in.check_connection_secret(InstanceType.GITHUB, "not-a-token")
    fragments = github.secret_fragments("ghp_x123")
    assert "ghp_x123" in fragments
    assert base64.b64encode(b"x-access-token:ghp_x123").decode() in fragments
