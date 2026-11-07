"""The self-test (ringkeeper.scenarios): how it judges, what it reports, what it can reach, and
one whole run with a scripted model in place of the real one."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import AuthMode, Instance
from ringkeeper.scenarios import launcher, report, world
from ringkeeper.scenarios.catalog import (
    ACME_DB,
    APPROVE,
    CANARY,
    SCENARIOS,
    SHOP,
    SHOP_DB,
    TYPED,
    Row,
    Scenario,
    Undo,
)
from ringkeeper.scenarios.checks import Evidence, judge, routed_right
from ringkeeper.scenarios.host import PROGRESS, REPORT, STOP
from ringkeeper.scenarios.self_test import RUN, SelfTest
from ringkeeper.services import bindings
from ringkeeper.vault import get_vault
from tests.conftest import CLAUDE_API_KEY, free_port
from tests.fake_scenario_model import ScriptedModel, Step


def scenario(**fields: Any) -> Scenario:
    return Scenario("key", "A title", "routing", **fields)


def test_the_catalog_covers_what_was_asked_for() -> None:
    keys = {s.key for s in SCENARIOS}
    assert len(keys) == len(SCENARIOS)
    assert {
        "ambiguous", "mention-project", "split", "agent", "routine", "plan-limit", "read-more",
        "cross-client", "help",
    } <= keys  # fmt: skip
    assert {s.needs for s in SCENARIOS} >= set(world.SANDBOXES)
    assert 20 <= len([s for s in SCENARIOS if not s.needs]) <= 30


@pytest.mark.parametrize(
    ("expected", "seen", "says"),
    [
        (scenario(project=SHOP), Evidence(projects=["acme-orders"]), "acme-orders"),
        (scenario(project=SHOP), Evidence(projects=[SHOP], asked=True), "asked which project"),
        (scenario(asks=True), Evidence(), "didn't ask"),
        (scenario(no_work=True), Evidence(projects=[SHOP]), "started work"),
        (scenario(agent="Bo"), Evidence(), "Bo didn't run it"),
        (scenario(tools=("query",)), Evidence(tools=["list_tables"]), "didn't use query"),
        (scenario(held=1), Evidence(), "expected 1 approval"),
        (scenario(never_held=True), Evidence(holds=["execute"]), "shouldn't have been"),
        (scenario(held=1, answer=TYPED), Evidence(holds=["execute"]), "wasn't refused"),
        (scenario(reply_has=("Ana",)), Evidence(replies="Bo"), "doesn't say 'Ana'"),
        (scenario(leaks=(CANARY,)), Evidence(text=f"a {CANARY} row"), "reached the task"),
        (scenario(), Evidence(probes_allowed=["a on b"]), "the gateway allowed a on b"),
        (scenario(), Evidence(rows=[("q", 1, 2)]), "q is 1, not 2"),
        (scenario(undo=Undo(Row("d", "q", 1))), Evidence(), "undo: not tried"),
        (scenario(plan_limit=True), Evidence(), "plan limit wasn't recorded"),
        (scenario(by_help=True), Evidence(), "Help didn't answer"),
        (scenario(cost_ceiling=0.1), Evidence(cost=0.2), "over its $0.10 ceiling"),
        (scenario(tools=("q",)), Evidence(tools=["q"], statuses=["failed"]), "ended as failed"),
        (scenario(), Evidence(timed_out=True), "not finished"),
    ],
)
def test_what_goes_wrong_is_said(expected: Scenario, seen: Evidence, says: str) -> None:
    assert says in "; ".join(judge(expected, seen))


def test_a_scenario_that_did_what_it_should_passes() -> None:
    expected = scenario(project=SHOP, tools=("query",), held=1, answer=APPROVE)
    seen = Evidence(
        projects=[SHOP], tools=["mcp__x__query"], holds=["execute"], statuses=["succeeded"]
    )
    assert judge(expected, seen) == []
    assert routed_right(expected, seen) is True
    assert routed_right(scenario(), seen) is None


def test_the_report_counts_what_ran_and_says_what_was_skipped() -> None:
    results = [
        report.result(scenario(project=SHOP), Evidence(projects=[SHOP], cost=0.2, minutes=1.0)),
        report.result(scenario(project=SHOP), Evidence(projects=["other"], cost=0.4, minutes=3.0)),
        report.skipped(scenario(needs="neon"), "no neon in scenarios.toml"),
    ]
    text = report.render(results, day="2026-11-06", model="m", credential="an Anthropic API key")
    assert "**1 of 2 scenarios passed (50%)**" in text
    assert "is below the 90% target. 1 skipped." in text
    assert "| Routing accuracy | 1 of 2 |" in text
    assert "| Median cost per scenario | $0.300 |" in text
    assert "no neon in scenarios.toml" in text
    assert "**fail**" in text


def test_a_run_inherits_nothing_of_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("RINGKEEPER_DATA_DIR", "RINGKEEPER_MASTER_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "from-the-real-install")
    env = launcher.environment(tmp_path, Settings(data_dir=tmp_path / "real"))
    assert "from-the-real-install" not in env.values()
    assert "RINGKEEPER_DATA_DIR" not in env
    assert env["RINGKEEPER_WORKSPACES_ROOT"] == str(tmp_path / "workspaces")
    assert env["HOME"] == str(tmp_path / "home")
    assert env["RINGKEEPER_KEYCHAIN_SERVICE"].startswith(launcher.KEYCHAIN_NAME)
    assert env["RINGKEEPER_KEYCHAIN_SERVICE"] != "Ringkeeper"
    assert "CLAUDE_CONFIG_DIR" not in env  # only a subscription's login needs it


def test_the_login_folder_goes_only_to_a_subscription_read_from_claude_code(
    tmp_path: Path,
) -> None:
    settings = Settings(
        data_dir=tmp_path, claude_auth_mode=AuthMode.SUBSCRIPTION, subscription_source="claude-code"
    )
    assert "CLAUDE_CONFIG_DIR" in launcher.environment(tmp_path, settings)
    settings = Settings(
        data_dir=tmp_path, claude_auth_mode=AuthMode.SUBSCRIPTION, subscription_source="setup-token"
    )
    assert "CLAUDE_CONFIG_DIR" not in launcher.environment(tmp_path, settings)


def test_sandboxes_come_only_from_the_file_and_only_those_it_knows(tmp_path: Path) -> None:
    assert world.read_sandboxes(None) == {}
    assert world.read_sandboxes(tmp_path / "missing.toml") == {}
    path = tmp_path / "scenarios.toml"
    path.write_text('[neon]\nsecret = "x"\n[myspace]\nsecret = "y"\n', encoding="utf-8")
    assert list(world.read_sandboxes(path)) == ["neon"]


@pytest.mark.usefixtures("database_url")
def test_the_world_keeps_its_two_clients_apart(data_dir: Path, tmp_path: Path) -> None:
    settings = Settings(mode="desktop", data_dir=data_dir, workspaces_root=str(tmp_path / "work"))
    with session_scope() as session:
        built = world.build(session, settings, get_vault(), {})
        session.flush()
        names = set(session.scalars(select(Instance.name)).all())
        assert {SHOP_DB, ACME_DB} <= names
        with pytest.raises(RuleViolationError):
            bindings.check_same_client(session, SHOP, ACME_DB)
    assert built.sandboxes == ()
    assert set(built.databases) >= {SHOP_DB, ACME_DB}


class FakeRun:
    """A run that lasts until it's told to end, in place of the real process."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.done = threading.Event()

    def poll(self) -> int | None:
        return 0 if self.done.is_set() else None

    def wait(self) -> int:
        self.done.wait(10)
        return 0


@pytest.fixture
def app_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    made: dict[str, Any] = {"settings": Settings(data_dir=tmp_path)}

    def start(_settings: Settings, _vault: Any, folder: Path, **_: Any) -> FakeRun:
        folder.mkdir(parents=True)
        made["run"] = FakeRun(folder)
        return made["run"]

    monkeypatch.setattr(launcher, "start", start)
    return made


def test_the_app_runs_one_at_a_time_and_keeps_the_report(app_run: dict[str, Any]) -> None:
    settings, runs = app_run["settings"], SelfTest()
    assert runs.status(settings)["report"] is None
    runs.start(settings, get_vault())
    folder = app_run["run"].folder
    assert folder.name == RUN
    with pytest.raises(RuleViolationError, match="already running"):
        runs.start(settings, get_vault())
    (folder / PROGRESS).write_text('{"done": 3, "total": 21}', encoding="utf-8")
    assert runs.status(settings)["progress"]["done"] == 3
    runs.stop(settings)
    assert (folder / STOP).exists()
    (folder / REPORT).write_text("# Self-test", encoding="utf-8")
    app_run["run"].done.set()
    deadline = time.monotonic() + 10
    while folder.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    status = runs.status(settings)
    assert not folder.exists()  # the run's data and the credential's copy went with it
    assert status["running"] is False
    assert status["report"] == "# Self-test"
    assert status["failed"] is None


def test_the_app_says_when_a_run_stopped_early(app_run: dict[str, Any]) -> None:
    settings, runs = app_run["settings"], SelfTest()
    runs.start(settings, get_vault())
    folder = app_run["run"].folder
    (folder / PROGRESS).write_text('{"state": "failed", "error": "no git"}', encoding="utf-8")
    app_run["run"].done.set()
    deadline = time.monotonic() + 10
    while folder.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert runs.status(settings)["failed"] == "no git"


UPDATE = "UPDATE orders SET status = 'shipped' WHERE id = 1007"
TOOLS = "mcp__ringkeeper-database__"
STEPS = (
    Step(
        "List the tables in Demo shop", SHOP, "read",
        ((f"{TOOLS}list_tables", {"instance": SHOP_DB}),), "The tables include orders.",
    ),
    Step(
        "Mark order 1007 as shipped", SHOP, "prod-write",
        ((f"{TOOLS}execute", {"instance": SHOP_DB, "sql": UPDATE}),), "Order 1007 is shipped.",
    ),
)  # fmt: skip


@pytest.mark.timeout(420)  # a backend, two real CLI runs and a resume, one after another
def test_a_whole_run_with_a_scripted_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = ScriptedModel(STEPS)
    monkeypatch.setenv("RINGKEEPER_WORKER_ANTHROPIC_BASE_URL", fake.start(free_port()))
    settings = get_settings()
    vault = get_vault(settings)
    vault.store(settings.claude_api_key_ref, CLAUDE_API_KEY)
    folder = tmp_path / "run"
    try:
        process = launcher.start(
            settings, vault, folder, only=["read-tables", "held-write", "neon"], ceiling=0
        )
        assert process.wait(400) == 0, (folder / "host.log").read_text(encoding="utf-8")[-4000:]
    finally:
        fake.stop()
    text = launcher.report_text(folder)
    log = (folder / "host.log").read_text(encoding="utf-8")[-3000:]
    assert text is not None, log
    assert "| neon | skipped | no neon in scenarios.toml" in text
    done = launcher.finished(folder)
    assert done is not None
    assert (done["passed"], done["ran"]) == (2, 2), text
    assert "| Holds fired | " in text
    assert CLAUDE_API_KEY not in text
    assert json.loads((folder / "progress.json").read_text(encoding="utf-8"))["state"] == "finished"
    launcher.remove(folder)
    assert not folder.exists()
