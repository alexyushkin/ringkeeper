"""What the router model must answer, validated with Pydantic.

Structure is checked by the models; registry rules (known projects, one owner
per task) by `check`, against the projects the router was shown.
"""

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ringkeeper.models import Complexity, TaskKind, TaskRisk

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Confidence = Annotated[float, Field(ge=0, le=1)]
# The kinds of connection a task can't be done without (services.needs.ROUTED).
NeedKind = Literal[
    "ads", "meta_ads", "google_ads", "email", "calendar", "records", "spreadsheet", "files",
    "database", "payments",
]  # fmt: skip


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Candidate(_Strict):
    project: Text
    confidence: Confidence


class RoutedTask(_Strict):
    project: Text
    confidence: Confidence
    # Other projects it might belong to instead, best first.
    alternatives: list[Candidate] = Field(default_factory=list[Candidate])
    # Other projects this same task would also have to change. Should be empty:
    # a task that spans projects ought to be split.
    touches: list[Text] = Field(default_factory=list[str])
    kind: TaskKind
    risk: TaskRisk
    complexity: Complexity
    outcome: Text
    prompt: Text
    # One of the project's agents, when the operator names it ("ask the follow-up agent").
    agent: str | None = None
    # False when any of its client's projects would do (the client's email, Google, Slack):
    # then it goes to the client's default project instead of asking which.
    project_specific: bool = True
    # When unsure between projects: one short line for the operator saying why.
    why: str = ""
    # Needs no client's connection (a web lookup, writing, summarizing pasted text): it runs
    # in the chosen project, or the operator's own client's default project.
    general: bool = False
    needs_web: bool = False  # it has to look something up on the web
    # Work in an outside account that no code change can do: the kind of connection it
    # can't be done without. A project with none of them is told so instead of starting.
    needs: list[NeedKind] = Field(default_factory=list[NeedKind])
    # The service the message names for the work ("Airtable", "Gmail"): only that one will do.
    service: str = ""
    # The project's own connections (instance names as listed) the work will use. One of them
    # linked to the project means nothing is missing.
    connections: list[str] = Field(default_factory=list[str])


class RouterOutput(_Strict):
    tasks: list[RoutedTask]
    reply: str = ""
    # A question about Ringkeeper itself (the operator's connections, projects, clients,
    # tasks, approvals, settings, costs): answered from Ringkeeper's own data, no task.
    about_ringkeeper: bool = False
    # It asks for work, but you can't tell which project: no tasks, and the reply says so.
    unplaced: bool = False


class InvalidOutputError(ValueError):
    pass


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


def parse(raw: str, owners: dict[str, str]) -> RouterOutput:
    """Parse and check one model answer. Raises InvalidOutputError with what to fix."""
    text = _FENCE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise InvalidOutputError("the answer must be one JSON object")
    try:
        output = RouterOutput.model_validate(json.loads(text[start : end + 1]))
    except (json.JSONDecodeError, ValueError) as error:
        raise InvalidOutputError(f"the JSON doesn't match the schema: {error}") from None
    check(output, owners)
    return output


def check(output: RouterOutput, owners: dict[str, str]) -> None:
    problems: list[str] = []
    for index, task in enumerate(output.tasks, 1):
        named = [task.project, *task.touches, *(c.project for c in task.alternatives)]
        unknown = sorted({p for p in named if p not in owners})
        if unknown:
            problems.append(f"task {index} names unknown projects: {', '.join(unknown)}")
            continue
        spanned = {owners[p] for p in [task.project, *task.touches]}
        if len(spanned) > 1:
            problems.append(
                f"task {index} spans owners {', '.join(sorted(spanned))}; never give one task "
                "to two owners, split it into one task per project"
            )
    if problems:
        raise InvalidOutputError("; ".join(problems))


def json_schema() -> str:
    return json.dumps(RouterOutput.model_json_schema(), separators=(",", ":"))
