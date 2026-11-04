"""A write is committed before the answer goes out."""

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ringkeeper.api import deps


def test_the_commit_comes_before_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    order: list[str] = []

    @contextmanager
    def session_scope() -> Generator[None]:
        yield None
        order.append("committed")

    monkeypatch.setattr(deps, "session_scope", session_scope)
    app = FastAPI()

    @app.post("/save")
    def save(session: deps.WriteSessionDep) -> dict[str, bool]:
        return {"saved": True}

    async def watched(scope: Any, receive: Any, send: Any) -> None:
        async def watching(message: Any) -> None:
            if message["type"] == "http.response.start":
                order.append("answered")
            await send(message)

        await app(scope, receive, watching)

    assert TestClient(watched).post("/save").status_code == 200
    assert order == ["committed", "answered"]
