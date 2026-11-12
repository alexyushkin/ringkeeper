"""Demo Co's made-up n8n, answered in place of its API for its own key only.

Two name groups of uneven size, each with a few duplicate names, over more than one page: the
shape that had a real count come out as 46 and 13 for 49 and 10. The real n8n adapter and the
real gateway handle it; only the network answer is made up.
"""

import json
from typing import Any

import httpx2

from ringkeeper.adapters import n8n

KEY = "demo-n8n-key"  # made up: only this module answers it
URL = "https://n8n.democo.example"
BIG, SMALL = "Bayside", "Mr. Gutter"


def _workflows() -> list[dict[str, Any]]:
    names = [f"{BIG} #{n}" for n in range(1, 47)] + [f"{BIG} #{n}" for n in (5, 12, 30)]
    names += [f"{SMALL} #{n}" for n in range(1, 10)] + [f"{SMALL} #4"]
    names += ["Nightly report", "Lead sync - website", "Invoice reminder", "Backup check",
              "Weekly digest"]  # fmt: skip
    return [
        {
            "id": f"wf{n:03d}", "name": name, "active": n % 3 != 0,
            "updatedAt": "2026-11-10T09:00:00.000Z",
            "tags": [{"name": "bayside" if name.startswith(BIG) else
                      "gutter" if name.startswith(SMALL) else "office"}],
        }
        for n, name in enumerate(names, start=1)
    ]  # fmt: skip


WORKFLOWS = _workflows()
COUNTS = {
    BIG: sum(w["name"].startswith(BIG) for w in WORKFLOWS),  # 49
    SMALL: sum(w["name"].startswith(SMALL) for w in WORKFLOWS),  # 10
}


def _answer(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path.removeprefix("/api/v1")
    params = request.url.params
    if path != "/workflows":
        return httpx2.Response(404, json={"message": "not found"})
    found = WORKFLOWS
    if params.get("active") is not None:
        found = [w for w in found if w["active"] == (params["active"] == "true")]
    if params.get("name"):
        found = [w for w in found if w["name"] == params["name"]]
    start = int(params.get("cursor") or 0)
    end = start + int(params.get("limit") or 100)
    more = str(end) if end < len(found) else None
    body = {"data": found[start:end], "nextCursor": more}
    return httpx2.Response(
        200, content=json.dumps(body), headers={"content-type": "application/json"}
    )


class Transport(httpx2.AsyncBaseTransport):
    def __init__(self) -> None:
        self.real = httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("x-n8n-api-key") == KEY:
            if request.method != "GET":
                return httpx2.Response(403, json={"message": "the demo n8n is read-only"})
            return _answer(request)
        return await self.real.handle_async_request(request)


def install() -> None:
    """In the self-test's own process only (see host)."""
    n8n.transport = Transport()
