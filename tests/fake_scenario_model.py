"""A scripted model for the self-test: the router's answers, and a worker's tool calls.

The real CLI and the real gateway run around it, so a run with this exercises everything the
self-test does except the model's judgement.
"""

import json
import uuid
from dataclasses import dataclass
from typing import Any, cast

from tests.fake_anthropic import FakeAnthropic


@dataclass(frozen=True)
class Step:
    needle: str  # part of the request, which also finds it in the worker's prompt
    project: str
    risk: str
    calls: tuple[tuple[str, dict[str, Any]], ...]  # (end of the tool's name, its arguments)
    answer: str


def _text(value: Any) -> str:
    return json.dumps(value)


def _tool_results(messages: list[dict[str, Any]]) -> int:
    found = 0
    for message in messages:
        content: Any = message.get("content")
        for block in cast(list[Any], content) if isinstance(content, list) else []:
            if isinstance(block, dict) and cast(dict[str, Any], block).get("type") == "tool_result":
                found += 1
    return found


class ScriptedModel(FakeAnthropic):
    def __init__(self, steps: tuple[Step, ...]) -> None:
        super().__init__({})
        self.steps = steps

    def _routing(self, step: Step) -> str:
        return json.dumps(
            {
                "tasks": [
                    {
                        "project": step.project, "confidence": 0.95, "kind": "data",
                        "risk": step.risk, "complexity": "low", "outcome": step.needle,
                        "prompt": step.needle,
                    }
                ]
            }
        )  # fmt: skip

    def _reply(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        system = _text(body.get("system"))
        words = _text(body.get("messages"))
        step = next((s for s in self.steps if s.needle in words), None)
        if step is None:
            return [{"type": "text", "text": "ok"}]
        if "Ringkeeper's router" in system:
            return [{"type": "text", "text": self._routing(step)}]
        done = _tool_results(body.get("messages", []))
        offered = [str(t.get("name")) for t in body.get("tools", [])]
        if done >= len(step.calls) or not offered:
            return [{"type": "text", "text": step.answer}]
        ending, arguments = step.calls[done]
        name = next((n for n in offered if n.endswith(ending)), ending)
        return [
            {
                "type": "tool_use", "id": f"toolu_{uuid.uuid4().hex[:24]}", "name": name,
                "input": arguments,
            }
        ]  # fmt: skip
