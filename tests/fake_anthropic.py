"""A stand-in for the Anthropic Messages API, so the real Claude Code CLI can
run in tests without a key or a bill.

The scripted "model" does one thing: on the first turn it calls the shell
tool Claude Code offered (Bash, or PowerShell on Windows without Git Bash)
with that tool's command from `commands`; once it sees the tool's result it
ends with a short reply. Every request is kept, so tests can check which
tools were offered.
"""

import json
import threading
import time
import uuid
from collections.abc import Iterator
from typing import Any, cast

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

SHELL_TOOLS = ("Bash", "PowerShell")
MODEL = "claude-sonnet-5-5"


class FakeAnthropic:
    def __init__(self, commands: dict[str, str]) -> None:
        self.commands = commands  # tool name -> the command to run with it
        self.requests: list[dict[str, Any]] = []
        self.server: uvicorn.Server | None = None
        self.url = ""

    # --- What the model says ---

    def shell_tools(self) -> list[str]:
        """The shell tools offered in the main conversation's requests."""
        found: list[str] = []
        for body in self.requests:
            for tool in body.get("tools", []):
                name = str(tool.get("name", ""))
                if name in SHELL_TOOLS and name not in found:
                    found.append(name)
        return found

    def _reply(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        tools = {str(t.get("name")) for t in body.get("tools", [])}
        shell = next((t for t in SHELL_TOOLS if t in tools), None)
        answered = _answered(body.get("messages", []))
        if shell is None or answered:
            return [{"type": "text", "text": "Done."}]
        return [
            {
                "type": "tool_use",
                "id": f"toolu_{uuid.uuid4().hex[:24]}",
                "name": shell,
                "input": {"command": self.commands[shell], "description": "Run the test"},
            }
        ]

    # --- The HTTP API ---

    async def messages(self, request: Request) -> Response:
        body: dict[str, Any] = await request.json()
        self.requests.append(body)
        blocks = self._reply(body)
        stop = "tool_use" if blocks[0]["type"] == "tool_use" else "end_turn"
        message = {
            "id": f"msg_{uuid.uuid4().hex[:24]}", "type": "message", "role": "assistant",
            "model": body.get("model", MODEL), "content": blocks, "stop_reason": stop,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5},
        }  # fmt: skip
        if not body.get("stream"):
            return JSONResponse(message)
        return StreamingResponse(_stream(message), media_type="text/event-stream")

    async def count_tokens(self, _request: Request) -> Response:
        return JSONResponse({"input_tokens": 10})

    async def other(self, request: Request) -> Response:
        return JSONResponse(
            {"type": "error", "error": {"type": "not_found_error", "message": request.url.path}},
            status_code=404,
        )

    def app(self) -> Starlette:
        return Starlette(
            routes=[
                Route("/v1/messages", self.messages, methods=["POST"]),
                Route("/v1/messages/count_tokens", self.count_tokens, methods=["POST"]),
                Route("/{path:path}", self.other, methods=["GET", "POST"]),
            ]
        )

    def start(self, port: int) -> str:
        self.server = uvicorn.Server(
            uvicorn.Config(self.app(), host="127.0.0.1", port=port, log_level="warning")
        )
        threading.Thread(target=self.server.run, daemon=True).start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("the fake Anthropic API didn't start")
            time.sleep(0.05)
        self.url = f"http://127.0.0.1:{port}"
        return self.url

    def stop(self) -> None:
        if self.server is not None:
            self.server.should_exit = True


def _answered(messages: list[dict[str, Any]]) -> bool:
    """Has a tool result come back since the model's last turn?"""
    for message in reversed(messages):
        if message.get("role") == "assistant":
            return False
        content: Any = message.get("content")
        blocks = cast(list[Any], content) if isinstance(content, list) else []
        if any(isinstance(b, dict) and cast(dict[str, Any], b).get("type") == "tool_result"
               for b in blocks):  # fmt: skip
            return True
    return False


def _event(kind: str, data: dict[str, Any]) -> str:
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n"


def _stream(message: dict[str, Any]) -> Iterator[str]:
    head: dict[str, Any] = {**message, "content": [], "stop_reason": None}
    yield _event("message_start", {"message": head})
    content: list[dict[str, Any]] = message["content"]
    for index, block in enumerate(content):
        if block["type"] == "text":
            yield _event(
                "content_block_start",
                {"index": index, "content_block": {"type": "text", "text": ""}},
            )
            yield _event(
                "content_block_delta",
                {"index": index, "delta": {"type": "text_delta", "text": block["text"]}},
            )
        else:
            start: dict[str, Any] = {**block, "input": {}}
            yield _event("content_block_start", {"index": index, "content_block": start})
            yield _event(
                "content_block_delta",
                {"index": index, "delta": {
                    "type": "input_json_delta", "partial_json": json.dumps(block["input"]),
                }},
            )  # fmt: skip
        yield _event("content_block_stop", {"index": index})
    yield _event(
        "message_delta",
        {"delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
         "usage": {"output_tokens": 5}},
    )  # fmt: skip
    yield _event("message_stop", {})
