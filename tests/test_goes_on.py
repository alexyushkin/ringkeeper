"""A task's result always answers the request as asked: a session Ringkeeper starts by itself
to go on adds what it says below the answer, never in its place."""

from types import SimpleNamespace
from typing import Any

import pytest

from ringkeeper.models import SessionRole, TaskStatus
from ringkeeper.services import goes_on, holds, interrupted, tasks
from ringkeeper.workers import manager

ANSWER = "Three emails from the flour supplier, the last on Tuesday."
EXTRA = "I can't write a tailored reply without more detail."


def second(prompt: str, before: TaskStatus = TaskStatus.SUCCEEDED) -> Any:
    """The task's second session, after a first that ended `before`."""
    first = SimpleNamespace(ordinal=1, status=before, prompt="Find the supplier's emails")
    row = SimpleNamespace(ordinal=2, prompt=prompt, role=SessionRole.WORKER)
    row.task = SimpleNamespace(sessions=[first, row])
    return row


@pytest.mark.parametrize(
    "prompt",
    [
        holds.report([]),
        manager.CONTINUE,
        interrupted.RESUME,
        f"{goes_on.RAISED} the user allowed $2.00 more. Continue the task from where you were.",
    ],
)
def test_going_on_by_itself_adds_below_the_answer(prompt: str) -> None:
    assert goes_on.by_itself(prompt)
    assert goes_on.result(ANSWER, EXTRA, second(prompt)) == f"{ANSWER}\n\n{EXTRA}"
    assert goes_on.result(ANSWER, ANSWER, second(prompt)) == ANSWER  # said once


def test_the_operators_own_words_get_their_own_answer() -> None:
    follow_up = second("Now draft a reply to the last one")
    assert not goes_on.by_itself(follow_up.prompt)
    assert goes_on.result(ANSWER, EXTRA, follow_up) == EXTRA


def test_after_a_session_that_didnt_answer_the_new_one_is_the_answer() -> None:
    # It failed, asked something, or never said anything: nothing to keep above.
    assert (
        goes_on.result("Let me look.", ANSWER, second(manager.CONTINUE, TaskStatus.FAILED))
        == ANSWER
    )
    assert (
        goes_on.result("Which supplier?", ANSWER, second(holds.report([]), TaskStatus.NEEDS_INPUT))
        == ANSWER
    )
    assert goes_on.result(None, ANSWER, second(holds.report([]))) == ANSWER


def test_why_a_session_started_says_no_words_of_its_own() -> None:
    first: Any = SimpleNamespace(ordinal=1, prompt="anything", role=SessionRole.WORKER)
    assert goes_on.why(first) == "the request"
    assert goes_on.why(second(holds.report([]))) == "held calls were decided, so it went on"
    assert goes_on.why(second("Draft it for Mrs Baker")) == "the operator's follow-up or answer"
    assert "Baker" not in goes_on.why(second("Draft it for Mrs Baker"))


def test_the_prompts_are_the_ones_the_task_service_sends() -> None:
    assert manager.CONTINUE == goes_on.CONTINUE
    assert interrupted.RESUME == goes_on.RESUMED
    assert tasks.RESUMABLE_STATUSES  # resumed sessions exist at all
