"""Sessions Ringkeeper starts by itself to go on with a task, and what their words do to its
result.

Ringkeeper continues a task on its own after held calls are decided, after Claude's side
failed for a moment, after a restart, and after the spending cap is raised. None of these is
a new request. So once a session has answered the request, what a later one of these says
goes below that answer, never in its place: a task's result always answers the request as
asked. A follow-up the operator sends, or an answer to the worker's question, is the
operator's own words, and its reply is the result.
"""

from ringkeeper.models import TaskStatus, WorkerSession

CONTINUE = "Claude's side failed for a moment. Continue where you left off."
RESUMED = "Ringkeeper restarted while you were working on this. Pick up where you left off."
DECIDED = "The calls that were waiting for the user's approval are decided:"
RAISED = "You were paused at the spending cap;"
BY_ITSELF = (CONTINUE, RESUMED, DECIDED, RAISED)

# What a session started for, as the task's support details say it.
WHY = {
    CONTINUE: "Claude's side failed for a moment, so it went on",
    RESUMED: "Ringkeeper restarted, so it went on",
    DECIDED: "held calls were decided, so it went on",
    RAISED: "the spending cap was raised, so it went on",
}


def by_itself(prompt: str) -> bool:
    """Whether Ringkeeper started this session on its own (not the operator's words)."""
    return prompt.startswith(BY_ITSELF)


def why(row: WorkerSession) -> str:
    """Why a session started, without its words (they can hold a client's data)."""
    if row.ordinal <= 1:
        return "the request"
    for start, reason in WHY.items():
        if row.prompt.startswith(start):
            return reason
    if row.role.value == "architect":
        return "the plan's next step"
    return "the operator's follow-up or answer"


def result(earlier: str | None, said: str, row: WorkerSession) -> str:
    """The task's result once `row` said `said`, given the result it had (`earlier`).

    After a session that answered (it succeeded), one Ringkeeper started by itself adds
    what it says below that answer, unless the answer already says it."""
    before = _previous(row)
    if (
        not earlier
        or not by_itself(row.prompt)
        or before is None
        or before.status is not TaskStatus.SUCCEEDED
    ):
        return said
    if said.strip() in earlier:
        return earlier
    return f"{earlier.rstrip()}\n\n{said}"


def _previous(row: WorkerSession) -> WorkerSession | None:
    earlier = [s for s in row.task.sessions if s.ordinal < row.ordinal]
    return max(earlier, key=lambda s: s.ordinal) if earlier else None
