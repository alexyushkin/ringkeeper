"""What the router model must answer, validated with Pydantic.

Structure is checked by the models; registry rules (known projects, one owner
per task) by `check`, against the projects the router was shown.
"""

import json
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from ringkeeper.models import Complexity, TaskKind, TaskRisk

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Confidence = Annotated[float, Field(ge=0, le=1)]


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


class RouterOutput(_Strict):
    tasks: list[RoutedTask]
    reply: str = ""


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
