"""The live key check (services.secrets_in.check_anthropic_key) against a local
stand-in for the Anthropic API: the workspace header, and Anthropic's own words."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar

import pytest

from ringkeeper.services import secrets_in

KEY = "sk-ant-usr-fake-check-key-0001"
NOT_SCOPED = "This API key is not scoped to a workspace. Send anthropic-workspace-id."


class FakeAnthropic(BaseHTTPRequestHandler):
    seen: ClassVar[list[str | None]] = []

    def do_GET(self) -> None:
        workspace = self.headers.get("anthropic-workspace-id")
        type(self).seen.append(workspace)
        if self.headers.get("x-api-key") != KEY:
            status, body = 401, {"type": "authentication_error", "message": "invalid x-api-key"}
        elif workspace is None:
            status, body = 400, {"type": "invalid_request_error", "message": NOT_SCOPED}
        else:
            status, body = 200, None
        payload = json.dumps(
            {"data": [], "has_more": False, "first_id": None, "last_id": None}
            if body is None
            else {"type": "error", "error": body}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def anthropic_api(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str | None]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeAnthropic)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    FakeAnthropic.seen = []
    yield FakeAnthropic.seen
    server.shutdown()


def test_a_key_that_needs_a_workspace_shows_anthropic_s_message(
    anthropic_api: list[str | None],
) -> None:
    with pytest.raises(secrets_in.RuleViolationError) as refused:
        secrets_in.check_anthropic_key(KEY)
    assert str(refused.value) == f"Anthropic said: {NOT_SCOPED}"
    assert KEY not in str(refused.value)
    secrets_in.check_anthropic_key(KEY, "wrkspc_01AbC")
    assert anthropic_api == [None, "wrkspc_01AbC"]


def test_a_wrong_key_says_so_in_anthropic_s_words(anthropic_api: list[str | None]) -> None:
    with pytest.raises(secrets_in.RuleViolationError, match="didn't accept this key: invalid"):
        secrets_in.check_anthropic_key("sk-ant-usr-some-other-key", "wrkspc_01AbC")
