"""A stand-in for Codex's app-server (tests/test_worker_codex.py): it speaks the same JSON-RPC
lines on stdin and stdout and plays one scripted turn, chosen by FAKE_CODEX_SCRIPT:

- answer: a connection's tool call, a file change, a command, then a final message.
- ask: the ask_user tool (answered by the worker), then waits for turn/interrupt.
- limit: the turn fails at the plan's limit.

What it was started with (arguments, thread params) is written to FAKE_CODEX_SEEN as JSON."""

import json
import os
import sys
import time
from typing import Any

SCRIPT = os.environ.get("FAKE_CODEX_SCRIPT", "answer")
SEEN_PATH = os.environ["FAKE_CODEX_SEEN"]
seen: dict[str, Any] = {"argv": sys.argv[1:], "env_token": os.environ.get("RK_GATEWAY_TOKEN")}


def save() -> None:
    """What it was told so far, whole or not at all: once a turn is over the worker may stop
    this process at any moment, so this is written before every message goes out."""
    with open(SEEN_PATH + ".part", "w", encoding="utf-8") as out:
        json.dump(seen, out)
    for _ in range(50):  # Windows may hold the old one a moment (an antivirus scan, say)
        try:
            os.replace(SEEN_PATH + ".part", SEEN_PATH)
            return
        except PermissionError:
            time.sleep(0.05)


def send(message: dict[str, Any]) -> None:
    save()
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def note(method: str, **params: Any) -> None:
    send({"method": method, "params": {"threadId": "thr-1", "turnId": "turn-1", **params}})


def done(item: dict[str, Any]) -> None:
    note("item/completed", item=item)


def usage(total: int) -> None:
    breakdown = {"totalTokens": total, "inputTokens": total - 5, "cachedInputTokens": 2,
                 "outputTokens": 5, "reasoningOutputTokens": 0}  # fmt: skip
    note("thread/tokenUsage/updated", tokenUsage={"total": breakdown, "last": breakdown})


def finish(status: str, error: dict[str, Any] | None = None) -> None:
    note("turn/completed", turn={"id": "turn-1", "status": status, "error": error, "items": []})


def play() -> None:
    if SCRIPT == "answer":
        saved = {"type": "text", "text": '{"saved_to": "attachments/orders.csv"}'}
        result = {"content": [saved], "structuredContent": None}
        done({"type": "mcpToolCall", "id": "call-1", "server": "ringkeeper-shop",
              "tool": "list_orders", "status": "completed", "arguments": {"status": "open"},
              "result": result, "error": None})  # fmt: skip
        done({"type": "fileChange", "id": "patch-1", "status": "completed",
              "changes": [{"path": "report.md", "kind": "add", "diff": "+hi"}]})  # fmt: skip
        done({"type": "commandExecution", "id": "cmd-1", "command": "pytest -q", "cwd": ".",
              "status": "completed", "aggregatedOutput": "1 passed", "exitCode": 0})  # fmt: skip
        usage(30)
        done({"type": "agentMessage", "id": "msg-1", "text": "Two open orders."})
        usage(60)
        finish("completed")
    elif SCRIPT == "ask":
        note("item/started", item={"type": "dynamicToolCall", "id": "dyn-1", "tool": "ask_user"})
        send({"id": 900, "method": "item/tool/call", "params": {
            "threadId": "thr-1", "turnId": "turn-1", "callId": "dyn-1", "tool": "ask_user",
            "arguments": {"question": "Test or production?"}}})  # fmt: skip
    elif SCRIPT == "crash":  # Codex exits in the middle of the turn
        sys.exit(1)
    elif SCRIPT == "limit":
        usage(10)
        finish("failed", {"message": "You've hit your usage limit.",
                          "codexErrorInfo": "usageLimitExceeded"})  # fmt: skip


for line in sys.stdin:
    message = json.loads(line)
    method, ident = message.get("method"), message.get("id")
    if method == "initialize":
        send({"id": ident, "result": {"userAgent": "fake"}})
    elif method in ("thread/start", "thread/resume"):
        seen[method] = message["params"]
        send({"id": ident, "result": {"thread": {"id": "thr-1"}}})
    elif method == "turn/start":
        seen["turn"] = message["params"]
        send({"id": ident, "result": {"turn": {"id": "turn-1", "status": "inProgress"}}})
        play()
    elif method == "turn/interrupt":
        seen["interrupted"] = True
        send({"id": ident, "result": {}})
        finish("interrupted")
    elif ident == 900:  # the worker's answer to ask_user
        seen["tool_answer"] = message["result"]
        done({"type": "dynamicToolCall", "id": "dyn-1", "tool": "ask_user", "status": "completed",
              "arguments": {"question": "Test or production?"},
              "contentItems": message["result"]["contentItems"], "success": True})  # fmt: skip
    save()
