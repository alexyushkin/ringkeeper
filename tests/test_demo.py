"""The demo client (services.demo) and Home's first steps (services.onboarding)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from ringkeeper.adapters import supabase
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.config import Settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Client, Env, Instance, InstanceType, Project, Task, Thread
from ringkeeper.services import demo, onboarding
from ringkeeper.services.calls import run_call
from ringkeeper.vault import Secret, VaultError, get_vault
from tests import test_web_api
from tests.helpers import bind, make_instance, make_project

owner = test_web_api.owner
admin = test_web_api.admin
setup_code = test_web_api.setup_code

pytestmark = pytest.mark.usefixtures("database_url")


def desktop(data_dir: Path, tmp_path: Path) -> Settings:
    return Settings(mode="desktop", data_dir=data_dir, workspaces_root=str(tmp_path / "work"))


def test_the_demo_adds_a_client_project_folder_and_database(data_dir: Path, tmp_path: Path) -> None:
    settings = desktop(data_dir, tmp_path)
    with session_scope() as session:
        made = demo.create(session, settings, get_vault())
    folder = Path(made.folder)
    assert (folder / "README.md").is_file()
    assert (folder / "notes" / "customer-feedback.md").is_file()
    assert Path(made.database).is_file()
    with session_scope() as session:
        project = session.scalars(select(Project).where(Project.name == demo.PROJECT)).one()
        assert project.owner == made.client
        assert session.get(Client, made.client).name == demo.CLIENT  # type: ignore[union-attr]
        instance = session.scalars(select(Instance).where(Instance.name == demo.CONNECTION)).one()
        assert instance.env is Env.PROD  # changes wait for approval
        assert [b.project.name for b in instance.bindings] == [demo.PROJECT]
        with pytest.raises(RuleViolationError, match="already there"):
            demo.create(session, settings, get_vault())


@pytest.mark.anyio
async def test_the_demo_database_reads_and_its_changes_are_held(
    data_dir: Path, tmp_path: Path
) -> None:
    with session_scope() as session:
        demo.create(session, desktop(data_dir, tmp_path), get_vault())
    caller = Caller(demo.PROJECT, "session-1", "token-1")

    def call(tool: str, **args: object) -> ToolCall:
        return ToolCall(caller, InstanceType.SUPABASE, tool, demo.CONNECTION, args)

    tables = await run_call(call("list_tables"), get_vault())
    assert [t["name"] for t in tables["tables"]] == [
        "customers",
        "order_items",
        "orders",
        "products",
    ]
    rows = await run_call(
        call("query", sql="SELECT status FROM orders WHERE id = %s", params=[1007]), get_vault()
    )
    assert rows["rows"] == [["paid"]]
    described = await run_call(call("describe_table", table="orders"), get_vault())
    assert "customer_id" in [c["name"] for c in described["columns"]]

    held = await run_call(
        call("execute", sql="UPDATE orders SET status = 'shipped' WHERE id = 1007"), get_vault()
    )
    assert held["status"] == "held_for_approval"
    # A read through execute still runs at once, read-only.
    read = await run_call(call("execute", sql="SELECT count(*) FROM customers"), get_vault())
    assert read["rows"] == [[8]]


@pytest.mark.anyio
async def test_a_sqlite_file_outside_the_demo_folder_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "other.db"
    elsewhere.write_bytes(b"")
    target = Target(
        "sneaky", Env.DEV, None, Secret("file://x", f"sqlite:///{elsewhere.as_posix()}")
    )
    with pytest.raises(ToolError, match="outside the demo folder"):
        await supabase.list_tables.handler(target)


def test_removing_the_demo_takes_everything_it_added(data_dir: Path, tmp_path: Path) -> None:
    settings = desktop(data_dir, tmp_path)
    with session_scope() as session:
        made = demo.create(session, settings, get_vault())
        make_project(session, "acme-web")  # someone else's, untouched
        thread = Thread(title="Try the demo", source="web")
        session.add(thread)
        session.flush()
        session.add(
            Task(project=demo.PROJECT, prompt="list tables", auth_mode="api_key", max_turns=5,
                 max_runtime_s=60, status="succeeded", thread_id=thread.id)
        )  # fmt: skip
    ref = f"file://ringkeeper/connections/{demo.CONNECTION}"
    assert get_vault().resolve(ref)
    with session_scope() as session:
        demo.remove(session, get_vault())
    with session_scope() as session:
        assert session.scalars(select(Project.name)).all() == ["acme-web"]
        assert session.scalars(select(Instance)).all() == []
        assert session.get(Client, made.client) is None
        assert session.scalars(select(Task)).all() == []
        assert session.scalars(select(Thread)).all() == []
        assert demo.current(session) is None
    assert not Path(made.folder).exists()
    assert not Path(made.database).exists()
    with pytest.raises(VaultError):
        get_vault().resolve(ref)


def test_the_checklist_and_suggestions_follow_what_is_there(data_dir: Path, tmp_path: Path) -> None:
    settings = desktop(data_dir, tmp_path)
    with session_scope() as session:
        steps = onboarding.checklist(session, claude_ready=False)
        assert (steps.project, steps.connection, steps.task, steps.done) == (
            False,
            False,
            False,
            False,
        )
        assert onboarding.suggestions(session, settings) == []
        make_project(session, "acme-web")
        make_instance(session, "acme-db", secret_ref="file://acme/db")
        make_instance(session, "acme-n8n", type=InstanceType.N8N, secret_ref="file://acme/n8n")
    bind("acme-web", "acme-db")
    bind("acme-web", "acme-n8n")
    with session_scope() as session:
        steps = onboarding.checklist(session, claude_ready=True)
        assert (steps.claude, steps.project, steps.connection, steps.task) == (
            True,
            True,
            True,
            False,
        )
        suggested = onboarding.suggestions(session, settings)
        assert suggested[0] == "List the tables in acme-web's database"
        assert all("delete" not in s.lower() and "update" not in s.lower() for s in suggested)
        onboarding.dismiss(session)
        assert onboarding.checklist(session, claude_ready=True).dismissed


def test_the_start_endpoint(owner: TestClient) -> None:
    client = owner
    answer = client.get("/start").json()
    assert set(answer["checklist"]) == {
        "claude",
        "project",
        "connection",
        "task",
        "done",
        "dismissed",
    }
    assert answer["demo"] is None
    assert client.post("/start/dismiss").status_code == 204
    assert client.get("/start").json()["checklist"]["dismissed"] is True
    assert answer["tour_seen"] is False
    assert client.post("/start/tour").status_code == 204
    assert client.get("/start").json()["tour_seen"] is True
    # Server installs have no demo (it's a desktop app thing).
    assert client.post("/demo").status_code == 404


def test_whats_new_shows_once_on_the_version_it_came_with() -> None:
    with session_scope() as session:
        onboarding.keep_notes(session, "0.1.0-beta.11", "- **Undo.** Revert a change.")
        assert onboarding.whats_new(session, "0.1.0-beta.10") is None  # not updated yet
        shown = onboarding.whats_new(session, "0.1.0-beta.11")
        assert shown == {"version": "0.1.0-beta.11", "notes": "- **Undo.** Revert a change."}
        onboarding.whats_new_seen(session)
        assert onboarding.whats_new(session, "0.1.0-beta.11") is None
