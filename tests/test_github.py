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
from sqlalchemy import select

from ringkeeper.adapters import github
from ringkeeper.adapters.base import Target
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType, ProjectInstance
from ringkeeper.services import bindings, instances, secrets_in
from ringkeeper.services.calls import RefusalError, approve, run_call
from ringkeeper.vault import Secret, get_vault
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
        if path.endswith("/check-runs"):
            runs = [
                {
                    "id": 5,
                    "name": "tests",
                    "status": "completed",
                    "conclusion": "failure",
                    "head_sha": "abcdef1234567890",
                    "html_url": "https://github.example/run/5",
                }
            ]
            return httpx2.Response(200, json={"total_count": 1, "check_runs": runs})
        if path.count("/") == 3 and path.startswith("/repos/"):
            return httpx2.Response(200, json={"full_name": path[7:], "default_branch": "main"})
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


def test_the_checks_on_the_default_branch_s_latest_commit(
    world: tuple[FakeGitHub, FakeGit, dict[str, Path]],
) -> None:
    fake, _git, _folders = world
    found = call("acme-web", "list_check_runs", "acme-gh", repo="acme-inc/website")
    assert found["branch"] == "main"
    assert found["check_runs"] == [{
        "id": 5, "name": "tests", "status": "completed", "conclusion": "failure",
        "sha": "abcdef123456", "completed": None, "url": "https://github.example/run/5",
    }]  # fmt: skip
    assert found["total"] == 1
    assert fake.seen[-1][2].endswith("/repos/acme-inc/website/commits/main/check-runs?per_page=30")
    with pytest.raises(RefusalError):  # the connection's scope is still the one repository
        call("acme-web", "list_check_runs", "acme-gh", repo="globex/app")


def test_lists_say_how_many_there_are_in_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full page isn't all of them: GitHub's own count says how many there are (its search
    for issues, which its issue list mixes with pull requests; its last page, one to a page,
    for the rest). A page that isn't full is all of them, and nothing more is asked."""
    asked: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        path, params = request.url.path, request.url.params
        if path == "/search/issues":
            open_only = "is:open" in params["q"]
            return httpx2.Response(200, json={"total_count": 12 if open_only else 40, "items": []})
        if params["per_page"] == "1":  # counting: the last page's number is how many
            last = {"/repos/acme-inc/website/pulls": 7 if params.get("state") == "open" else 9}
            link = (
                f'<https://api.github.com{path}?per_page=1&page={last.get(path, 25)}>; rel="last"'
            )
            return httpx2.Response(200, json=[{}], headers={"link": link})
        state = "closed" if "pulls" in path else "open"
        item = {"number": 1, "title": "x", "state": state, "name": "main", "sha": "abc"}
        return httpx2.Response(200, json=[item] * int(params["per_page"]))

    monkeypatch.setattr(github, "transport", httpx2.MockTransport(handle))
    tgt = Target("acme-gh", Env.DEV, None, Secret("file://acme/gh", TOKENS["acme-gh"]))
    repo = "acme-inc/website"

    def run(tool: str, **args: Any) -> Any:
        async def go() -> Any:
            return await github.ADAPTER.tool(tool).handler(tgt, **args)

        return asyncio.run(go())

    issues = run("list_issues", repo=repo, state="all", labels="bug, urgent", limit=2)
    assert (len(issues["issues"]), issues["total"], issues["open"]) == (2, 40, 12)
    searched = [r.url.params["q"] for r in asked if r.url.path == "/search/issues"]
    assert searched == [
        f'repo:{repo} is:issue label:"bug" label:"urgent"',
        f'repo:{repo} is:issue label:"bug" label:"urgent" is:open',
    ]
    pulls = run("list_pulls", repo=repo, state="all", limit=2)
    assert (pulls["total"], pulls["open"]) == (9, 7)
    assert run("list_branches", repo=repo, limit=2)["total"] == 25
    assert run("list_commits", repo=repo, limit=2)["total"] == 25
    assert run("list_repos", limit=2)["total"] == 25

    def one_pull(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        return httpx2.Response(200, json=[{"number": 1, "state": "open"}])

    asked.clear()
    monkeypatch.setattr(github, "transport", httpx2.MockTransport(one_pull))
    one = run("list_pulls", repo=repo, state="all", limit=5)
    assert (one["total"], one["open"], len(asked)) == (1, 1, 1)


def test_pull_requests_with_a_label_are_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """GitHub's pull list can't filter by label: its issue list can, and its search counts
    them, so "how many PRs are labelled release" is a total, not a hand count."""
    asked: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        asked.append(request)
        if request.url.path == "/search/issues":
            open_only = "is:open" in request.url.params["q"]
            return httpx2.Response(200, json={"total_count": 12 if open_only else 31, "items": []})
        assert request.url.path == "/repos/acme-inc/website/issues"
        assert request.url.params["labels"] == "release"
        size = int(request.url.params["per_page"])
        pull: dict[str, Any] = {"title": "x", "state": "open", "pull_request": {}}
        items = [{"number": n, **pull} for n in range(size)]
        return httpx2.Response(200, json=items)

    monkeypatch.setattr(github, "transport", httpx2.MockTransport(handle))
    tgt = Target("acme-gh", Env.DEV, None, Secret("file://acme/gh", TOKENS["acme-gh"]))

    async def go() -> Any:
        tool = github.ADAPTER.tool("list_pulls")
        return await tool.handler(tgt, repo="acme-inc/website", state="all", labels="release",
                                  limit=2)  # fmt: skip

    pulls = asyncio.run(go())
    assert (len(pulls["pulls"]), pulls["total"], pulls["open"]) == (2, 31, 12)
    assert [r.url.params["q"] for r in asked if r.url.path == "/search/issues"] == [
        'repo:acme-inc/website is:pr label:"release"',
        'repo:acme-inc/website is:pr label:"release" is:open',
    ]


def test_a_link_pinned_to_several_repositories(
    world: tuple[FakeGitHub, FakeGit, dict[str, Path]],
) -> None:
    fake, _, _ = world
    bind("acme-web", "acme-gh", scope={"repo": "acme-inc/website, acme-inc/api"}, is_default=True)
    with session_scope() as session:
        stored = (
            session.scalars(
                select(ProjectInstance).where(
                    ProjectInstance.instance_id == instances.get_instance(session, "acme-gh").id
                )
            )
            .one()
            .scope
        )
    assert stored == {"repo": "acme-inc/website,acme-inc/api"}
    assert (
        call("acme-web", "list_issues", repo="acme-inc/api")["issues"][0]["title"]
        == "in acme-inc/api"
    )
    assert call("acme-web", "list_issues", repo="ACME-INC/Website")["issues"]  # as GitHub: any case
    with pytest.raises(
        RefusalError, match="limited to the repositories 'acme-inc/website', 'acme-inc/api'"
    ):
        call("acme-web", "list_issues", repo="globex/app")
    with pytest.raises(RefusalError, match="name one as repo"):
        call("acme-web", "list_issues")  # which one isn't guessed
    listed = call("acme-web", "list_repos")["repos"]
    assert [r["repo"] for r in listed] == ["acme-inc/website", "acme-inc/api"]
    del fake


def test_only_github_takes_several_values() -> None:
    from ringkeeper import remote  # noqa: PLC0415

    github_spec = remote.NATIVE["github"]
    assert remote.check_scope(github_spec, {"repo": " a/b , c/d , a/b "}) == {"repo": "a/b,c/d"}
    with pytest.raises(remote.ScopeError, match="Only one airtable base"):
        remote.check_scope(remote.NATIVE["airtable"], {"base": "app1,app2"})


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
    # list_repos lists only the pinned repository, not every one the token reaches.
    assert call("acme-web", "list_repos", "acme-gh")["repos"] == [
        {"repo": "acme-inc/website", "private": None}
    ]
    assert not any(url.endswith("/user/repos?per_page=30&sort=updated") for *_, url in fake.seen)
    with (
        session_scope() as session,
        pytest.raises(RuleViolationError, match="only be used by that client's projects"),
    ):
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
