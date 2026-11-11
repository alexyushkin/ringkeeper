"""A task's result is its last message, kept whole: a 105-row table keeps every row. Cards
and chat previews keep their own short previews; only a runaway result is cut, at a line
break, saying where the rest is."""

import pytest
from fastapi.testclient import TestClient

from ringkeeper.channels import news
from ringkeeper.db import session_scope
from ringkeeper.services import tasks
from ringkeeper.workers.manager import CUT_HERE, SUMMARY_CHARS, kept_result
from ringkeeper.workers.runtime import WorkerSpec
from tests import test_web_api
from tests.fake_runtime import Script, line, workspace_line
from tests.test_manager import manager, run_task

setup_code, admin, owner = test_web_api.setup_code, test_web_api.admin, test_web_api.owner

ROWS = 105
TABLE = "\n".join(
    [
        "Here are the workflows:",
        "",
        "| # | Workflow | Active |",
        "|---|---|---|",
        *(
            f"| {n} | Workflow number {n} with a fairly long name | yes |"
            for n in range(1, ROWS + 1)
        ),
    ]
)


def said(text: str) -> str:
    return line(
        "sdk.result",
        message={
            "_kind": "ResultMessage", "subtype": "success", "is_error": False, "result": text,
            "num_turns": 2, "session_id": "sdk-1", "total_cost_usd": 0.01,
        },
    )  # fmt: skip


@pytest.mark.usefixtures("task_registry")
def test_a_105_row_table_is_kept_to_its_last_row(owner: TestClient) -> None:
    assert len(TABLE) > 4000  # what used to be the cut

    def script(_spec: WorkerSpec) -> Script:
        return Script(after=[said(TABLE), workspace_line([])])

    m, _ = manager(script)
    task_id = run_task()
    m.tick()
    m.wait_idle()
    with session_scope() as session:
        summary = tasks.get_task(session, task_id).summary or ""
        assert summary == TABLE
        assert summary.endswith(
            f"| {ROWS} | Workflow number {ROWS} with a fairly long name | yes |"
        )
        in_chat = news.result_text(tasks.get_task(session, task_id), "https://example.test/t")
    assert len(in_chat) < 2000  # the chat keeps its own short preview, with the link
    assert in_chat.endswith("The rest is in Ringkeeper: https://example.test/t")
    page = owner.get(f"/tasks/{task_id}").json()
    assert page["summary"] == TABLE
    [card] = [c for c in owner.get("/tasks").json() if c["id"] == task_id]
    assert len(card["summary"]) <= 2002
    assert card["summary"].endswith("\n…")


def test_only_a_runaway_result_is_cut_at_a_line_break() -> None:
    assert kept_result(TABLE, "/task?id=x") == TABLE
    runaway = "\n".join(f"line {n} " + "x" * 90 for n in range(4000))
    assert len(runaway) > SUMMARY_CHARS
    kept = kept_result(runaway, "/task?id=x")
    head, tail = kept.rsplit("\n\n", 1)
    assert tail == f"{CUT_HERE}: [open the task](/task?id=x)."
    assert runaway.startswith(head)
    assert runaway[len(head)] == "\n"  # cut where a line ends, not mid-row
    assert len(head) <= SUMMARY_CHARS
