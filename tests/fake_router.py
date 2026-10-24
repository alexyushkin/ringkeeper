"""A router model that answers from a script, for router tests."""

import json
from dataclasses import dataclass, field
from typing import Any

from ringkeeper.router.model import Completion


@dataclass
class FakeModel:
    """Answers with scripted outputs, one per call, and records every call."""

    answers: list[str | dict[str, Any]]
    calls: list[tuple[str, str, str]] = field(default_factory=list[tuple[str, str, str]])

    def complete(self, system: str, prompt: str, model: str) -> Completion:
        self.calls.append((system, prompt, model))
        answer = self.answers.pop(0)
        return Completion(answer if isinstance(answer, str) else json.dumps(answer), 0.001)


def routed(
    project: str,
    confidence: float = 0.95,
    *,
    kind: str = "data",
    complexity: str = "low",
    prompt: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "project": project,
        "confidence": confidence,
        "kind": kind,
        "risk": "read",
        "complexity": complexity,
        "outcome": f"done in {project}",
        "prompt": prompt or f"Work in {project}.",
        **extra,
    }


def output(*tasks: dict[str, Any], reply: str = "") -> dict[str, Any]:
    return {"tasks": list(tasks), "reply": reply}
