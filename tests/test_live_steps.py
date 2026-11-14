"""A task's Steps count its tool calls as they happen: a running task shows them, not 0."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ringkeeper.db import session_scope
from ringkeeper.models import TaskStatus
from ringkeeper.services import tasks
from ringkeeper.workers.manager import tool_calls
from tests import test_web_api
from tests.fake_runtime import Script, line, result_line, workspace_line
from tests.test_manager import drive, manager, run_task, session_of, task

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner


def calls(*names: str, said: str = "") -> str:
    blocks: list[dict[str, Any]] = [{"_kind": "TextBlock", "text": said}] if said else []
    blocks += [{"_kind": "ToolUseBlock", "name": n, "input": {}} for n in names]
    return line("sdk.assistant", message={"content": blocks})


def test_a_message_counts_its_tool_calls() -> None:
    assert tool_calls({"content": [{"_kind": "ToolUseBlock"}, {"_kind": "TextBlock"}]}) == 1
    assert tool_calls({"content": "just text"}) == 0
    assert tool_calls({}) == 0


@pytest.mark.usefixtures("task_registry")
def test_steps_go_up_while_it_runs(owner: TestClient) -> None:
    # Real use: the task page said "Steps 0" after three tool calls.
    m, _ = manager(
        lambda _spec: Script(
            before=[calls("search_mail", said="Looking."), calls("read_mail", "read_mail")],
            delay=30,  # it's still running, until it's stopped below
            after=[result_line(turns=4), workspace_line([])],
        )
    )
    task_id = run_task()
    m.tick()
    # The lines are read as they come.
    drive(m, lambda: session_of(task_id).tool_calls == 3, timeout=10, idle=False)
    card = owner.get(f"/tasks/{task_id}").json()
    assert card["status"] == "running"
    assert card["steps"] == 3
    assert [s["steps"] for s in card["session_list"]] == [3]
    with session_scope() as session:
        tasks.request_stop(session, task_id, by="ops")
    drive(m, lambda: task(task_id).status is TaskStatus.STOPPED)
    assert session_of(task_id).tool_calls == 3  # kept once it ends
