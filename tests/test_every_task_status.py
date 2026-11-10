"""Every task status, through every place that sorts statuses: the morning digest, the client's
month report, the self-test, the cleanup of unfinished tasks, and what a pass is told. A status
added later can't be missed: this fails until each place says what it does with it."""

from datetime import UTC, datetime

import pytest

from ringkeeper.models import TERMINAL_STATUSES, Task, TaskStatus
from ringkeeper.scenarios import driver
from ringkeeper.services import cleanup, client_month, digest, pass_tasks

ENDED = sorted(TERMINAL_STATUSES)


@pytest.mark.parametrize("status", list(TaskStatus))
def test_each_status_is_sorted_everywhere(status: TaskStatus) -> None:
    ended = status in TERMINAL_STATUSES
    # The digest: an ending is a problem unless it's done or stopped by you.
    assert (status in digest.FAILED) is (
        ended and status not in (TaskStatus.SUCCEEDED, TaskStatus.STOPPED)
    )
    # The month report has a word for every ending.
    if ended:
        assert status in client_month.STATUS_WORDS, status
    # The self-test waits for every ending (and a question), and never stops an ended task.
    assert (status in driver.RESTING) is (ended or status is TaskStatus.NEEDS_INPUT)
    # Cleanup: every ending but done is unfinished.
    assert (status in cleanup.UNFINISHED) is (ended and status is not TaskStatus.SUCCEEDED)
    # A pass is told something specific for every ending, and the reason when there is one.
    task = Task(project="p", prompt="x", status=status, error="why", finished_at=datetime.now(UTC))
    said = pass_tasks._state(task, [])  # pyright: ignore[reportPrivateUsage]
    assert said in pass_tasks.SAYS
    if ended:
        assert said not in ("running", "waiting"), status


def test_not_done_reads_as_itself() -> None:
    task = Task(project="p", prompt="x", status=TaskStatus.NOT_DONE, error="No email connection.")
    assert pass_tasks._state(task, []) == "not done"  # pyright: ignore[reportPrivateUsage]
    assert client_month.STATUS_WORDS[TaskStatus.NOT_DONE] == "not done"
    assert ENDED
