"""Fake Slack and Discord: their web APIs (httpx2.MockTransport) and a real local
websocket for button presses (Slack's Socket Mode, Discord's gateway)."""

import itertools
import json
import queue
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import httpx2
from websockets.exceptions import ConnectionClosed
from websockets.sync.server import Server, ServerConnection, serve

SLACK_BOT = "xoxb-1111-2222-fakebottokenvalue"
SLACK_APP = "xapp-1-A111-2222-fakeapptokenvalue"
SLACK_API = "https://slack.test/api"
DISCORD_BOT = "MTExMTExMTExMTExMTExMTEx.Gabcde.fake-discord-bot-token-value-xyz"
DISCORD_API = "https://discord.test/api/v10"


class Socket:
    """One websocket server; the test pushes frames, the fake records what comes back."""

    def __init__(self, greeting: dict[str, Any] | None) -> None:
        self.greeting = greeting
        self.outgoing: queue.Queue[dict[str, Any]] = queue.Queue()
        self.received: list[dict[str, Any]] = []
        self.connections = 0
        self.server: Server = serve(self._serve, "127.0.0.1", 0)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        host, port = self.server.socket.getsockname()[:2]
        return f"ws://{host}:{port}"

    def _serve(self, connection: ServerConnection) -> None:
        self.connections += 1
        if self.greeting is not None:
            connection.send(json.dumps(self.greeting))
        while True:
            try:
                frame = self.outgoing.get(timeout=0.05)
                connection.send(json.dumps(frame))
            except queue.Empty:
                pass
            except ConnectionClosed:
                return
            try:
                self.received.append(json.loads(connection.recv(timeout=0.05)))
            except TimeoutError:
                continue
            except ConnectionClosed:
                return

    def close(self) -> None:
        self.server.shutdown()


def wait_for(check: Any, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting")


class FakeSlack:
    def __init__(self) -> None:
        self.socket = Socket({"type": "hello"})
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.replies: list[dict[str, Any]] = []
        self.tokens: list[str] = []
        self._ts = itertools.count(1)
        self._envelope = itertools.count(1)

    def posted(self) -> list[dict[str, Any]]:
        return [body for method, body in self.calls if method == "chat.postMessage"]

    def updated(self) -> list[dict[str, Any]]:
        return [body for method, body in self.calls if method == "chat.update"]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.startswith("/respond/"):
            self.replies.append(body)
            return httpx2.Response(200, text="ok")
        method = request.url.path.rsplit("/", 1)[-1]
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        self.tokens.append(token)
        self.calls.append((method, body))
        wanted = SLACK_APP if method == "apps.connections.open" else SLACK_BOT
        if token != wanted:
            return httpx2.Response(200, json={"ok": False, "error": "invalid_auth"})
        result: dict[str, Any]
        match method:
            case "auth.test":
                result = {"team": "Acme Inc", "user": "ringkeeper", "user_id": "UBOT"}
            case "apps.connections.open":
                result = {"url": self.socket.url}
            case "conversations.list":
                result = {"channels": [{"id": "C1", "name": "ops"}, {"id": "C2", "name": "alerts"}]}
            case "conversations.open":
                result = {"channel": {"id": "D1"}}
            case "chat.postMessage":
                result = {"channel": body["channel"], "ts": f"1700000000.{next(self._ts):06d}"}
            case "chat.update":
                result = {"channel": body["channel"], "ts": body["ts"]}
            case _:
                result = {}
        return httpx2.Response(200, json={"ok": True, **result})

    def press(self, value: str, user: str) -> int:
        """A button pressed in Slack: an interactive envelope over Socket Mode."""
        ident = f"env-{next(self._envelope)}"
        payload = {
            "type": "block_actions",
            "user": {"id": user, "username": user.lower()},
            "actions": [{"action_id": "rk-0", "value": value}],
            "response_url": f"https://slack.test/respond/{ident}",
        }
        before = len(self.replies)
        self.socket.outgoing.put({"envelope_id": ident, "type": "interactive", "payload": payload})
        wait_for(lambda: len(self.replies) > before)
        assert {"envelope_id": ident} in self.socket.received  # acknowledged
        return len(self.replies) - 1


class FakeDiscord:
    def __init__(self) -> None:
        self.socket = Socket({"op": 10, "d": {"heartbeat_interval": 45000}})
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.callbacks: list[dict[str, Any]] = []
        self._ids = itertools.count(100)
        self._seq = itertools.count(1)

    def posted(self) -> list[dict[str, Any]]:
        return [b or {} for m, p, b in self.calls if m == "POST" and p.endswith("/messages")]

    def edited(self) -> list[dict[str, Any]]:
        return [b or {} for m, _p, b in self.calls if m == "PATCH"]

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path.removeprefix("/api/v10/")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        if request.headers.get("authorization") != f"Bot {DISCORD_BOT}":
            return httpx2.Response(401, json={"message": "401: Unauthorized", "code": 0})
        if path.startswith("interactions/"):
            self.callbacks.append(body or {})
            return httpx2.Response(204)
        match (request.method, path):
            case ("GET", "users/@me"):
                return httpx2.Response(200, json={"id": "900", "username": "Ringkeeper"})
            case ("GET", "oauth2/applications/@me"):
                return httpx2.Response(200, json={"id": "800", "name": "Ringkeeper"})
            case ("GET", "users/@me/guilds"):
                return httpx2.Response(200, json=[{"id": "601", "name": "Acme"}])
            case ("GET", "guilds/601/channels"):
                return httpx2.Response(
                    200,
                    json=[
                        {"id": "501", "name": "ops", "type": 0},
                        {"id": "509", "name": "Voice", "type": 2},
                    ],
                )
            case ("POST", "users/@me/channels"):
                return httpx2.Response(200, json={"id": "701", "type": 1})
            case ("GET", "gateway/bot"):
                return httpx2.Response(200, json={"url": self.socket.url})
            case ("POST", _) if path.endswith("/messages"):
                return httpx2.Response(200, json={"id": str(next(self._ids))})
            case ("PATCH", _):
                return httpx2.Response(200, json={"id": path.rsplit("/", 1)[-1]})
            case _:
                return httpx2.Response(404, json={"message": "Unknown"})

    def identified(self) -> dict[str, Any] | None:
        return next((f["d"] for f in self.socket.received if f.get("op") == 2), None)

    def press(self, custom_id: str, user: str) -> int:
        """A button pressed in Discord: INTERACTION_CREATE over the gateway."""
        wait_for(lambda: self.identified() is not None)
        before = len(self.callbacks)
        interaction = {
            "id": f"i{next(self._ids)}",
            "token": "interaction-token",
            "type": 3,
            "data": {"custom_id": custom_id, "component_type": 2},
            "member": {"user": {"id": user, "username": f"user{user}"}},
            "channel_id": "501",
        }
        frame = {"op": 0, "t": "INTERACTION_CREATE", "s": next(self._seq), "d": interaction}
        self.socket.outgoing.put(frame)
        wait_for(lambda: len(self.callbacks) > before)
        return len(self.callbacks) - 1


@contextmanager
def listening(api: Any, on_press: Any) -> Generator[threading.Event]:
    """The channel's listener on its own thread, for as long as the block runs."""
    stop = threading.Event()
    thread = threading.Thread(target=api.listen, args=(stop, on_press), daemon=True)
    thread.start()
    try:
        yield stop
    finally:
        stop.set()
        thread.join(timeout=5)
