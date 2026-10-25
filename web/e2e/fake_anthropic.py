"""A stand-in for the Anthropic API, for the end-to-end tests only.

The setup wizard checks a pasted API key with one `models.list` call. The
api container's ANTHROPIC_BASE_URL points here in the e2e stack (never in a
real install), so the check runs for real without a real key. Keys that
start with sk-ant-api03-e2e are accepted; any other key is rejected.
"""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ACCEPTED_PREFIX = "sk-ant-api03-e2e"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict[str, object]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if not self.path.startswith("/v1/models"):
            self._send(404, {"type": "error", "error": {"type": "not_found_error"}})
            return
        if not self.headers.get("x-api-key", "").startswith(ACCEPTED_PREFIX):
            self._send(
                401,
                {
                    "type": "error",
                    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
                },
            )
            return
        model = {"type": "model", "id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5",
                 "created_at": "2026-01-01T00:00:00Z"}  # fmt: skip
        self._send(200, {"data": [model], "has_more": False, "first_id": model["id"],
                         "last_id": model["id"]})  # fmt: skip

    def log_message(self, format: str, *args: object) -> None:
        pass


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()  # noqa: S104
