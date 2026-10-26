"""A small in-memory stand-in for the n8n public API, as an httpx2.MockTransport."""

import json
from dataclasses import dataclass, field
from typing import Any

import httpx2

API_KEY = "n8n-api-key-fake-4f2a91"  # a fake; long enough to scrub safely


@dataclass
class FakeN8n:
    api_key: str = API_KEY
    workflows: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])
    executions: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    requests: list[httpx2.Request] = field(default_factory=list[httpx2.Request])
    clock: int = 0

    def add_workflow(self, workflow_id: str, name: str, *, active: bool = False) -> None:
        self.workflows[workflow_id] = {
            "id": workflow_id,
            "name": name,
            "active": active,
            "nodes": [{"name": "Start", "type": "n8n-nodes-base.manualTrigger"}],
            "connections": {},
            "settings": {},
            "tags": [{"id": "1", "name": "acme"}],
            "updatedAt": self._tick(),
        }

    def _tick(self) -> str:
        self.clock += 1
        return f"2026-10-24T10:00:{self.clock:02d}.000Z"

    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self.handle)

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.headers.get("X-N8N-API-KEY") != self.api_key:
            return httpx2.Response(401, json={"message": "unauthorized"})
        path = request.url.path.removeprefix("/api/v1")
        parts = path.strip("/").split("/")
        method = request.method
        if parts == ["workflows"] and method == "GET":
            items = list(self.workflows.values())
            if request.url.params.get("active") is not None:
                wanted = request.url.params["active"] == "true"
                items = [w for w in items if w["active"] == wanted]
            return httpx2.Response(200, json={"data": items, "nextCursor": None})
        if parts[0] == "workflows" and len(parts) >= 2:
            workflow = self.workflows.get(parts[1])
            if workflow is None:
                return httpx2.Response(404, json={"message": "Not Found"})
            if len(parts) == 2 and method == "GET":
                return httpx2.Response(200, json=workflow)
            if len(parts) == 2 and method == "PUT":
                body = json.loads(request.content)
                unknown = set(body) - {"name", "nodes", "connections", "settings"}
                if unknown:
                    return httpx2.Response(400, json={"message": f"read-only: {sorted(unknown)}"})
                workflow.update(body, updatedAt=self._tick())
                return httpx2.Response(200, json=workflow)
            if len(parts) == 3 and method == "POST" and parts[2] in ("activate", "deactivate"):
                workflow.update(active=parts[2] == "activate", updatedAt=self._tick())
                return httpx2.Response(200, json=workflow)
        if parts == ["executions"] and method == "GET":
            wid = request.url.params.get("workflowId")
            items = [e for e in self.executions if wid is None or e["workflowId"] == wid]
            return httpx2.Response(200, json={"data": items, "nextCursor": "page-2"})
        if parts[0] == "executions" and len(parts) == 2 and method == "GET":
            for execution in self.executions:
                if execution["id"] == parts[1]:
                    detailed = request.url.params.get("includeData") == "true"
                    return httpx2.Response(
                        200, json={**execution, "data": {} if detailed else None}
                    )
            return httpx2.Response(404, json={"message": "Not Found"})
        if parts[0] == "executions" and len(parts) == 3 and method == "POST":
            if not any(e["id"] == parts[1] for e in self.executions):
                return httpx2.Response(404, json={"message": "Not Found"})
            if parts[2] == "retry":
                retry = {
                    "id": str(1000 + len(self.executions)),
                    "mode": "retry",
                    "retryOf": parts[1],
                }
                return httpx2.Response(200, json=retry)
            if parts[2] == "stop":
                return httpx2.Response(200, json={"id": parts[1], "status": "canceled"})
        return httpx2.Response(404, json={"message": f"no route {method} {path}"})
