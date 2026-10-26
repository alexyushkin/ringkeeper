"""The client/project switcher's filters: every list narrows to a client or a
project, and every item says whose it is."""

import pytest
from fastapi.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.models import DecisionKind
from ringkeeper.services import inbox, tasks, threads
from tests.helpers import make_project
from tests.test_web_api import (  # noqa: F401 (fixtures)
    admin,  # pyright: ignore[reportUnusedImport]
    owner,  # pyright: ignore[reportUnusedImport]
    setup_code,  # pyright: ignore[reportUnusedImport]
)


@pytest.fixture
def two_clients(owner: TestClient) -> dict[str, str]:  # noqa: F811
    made: dict[str, str] = {}
    with session_scope() as session:
        make_project(session, "acme-web", owner="Acme")
        make_project(session, "acme-app", owner="Acme")
        make_project(session, "globex-portal", owner="globex")
        for project in ("acme-web", "acme-app", "globex-portal"):
            thread = threads.post_message(
                session, f"work for {project}", author="owner", source="web"
            ).thread
            task = tasks.create_task(
                session, get_settings(), project=project, prompt="do it", thread_id=thread.id
            )
            inbox.create(
                session, get_settings(), DecisionKind.QUESTION, {"question": "?"},
                project=project, task=task,
            )  # fmt: skip
            made[project] = str(thread.id)
    return made


def test_lists_narrow_to_a_client_or_a_project(
    owner: TestClient,  # noqa: F811
    two_clients: dict[str, str],
) -> None:
    acme = owner.get("/tasks", params={"client": "acme"}).json()  # any case
    assert sorted(t["project"] for t in acme) == ["acme-app", "acme-web"]
    assert {t["client"] for t in acme} == {"Acme"}
    web = owner.get("/tasks", params={"project": "acme-web"}).json()
    assert [t["project"] for t in web] == ["acme-web"]
    assert len(owner.get("/tasks").json()) == 3

    listed = owner.get("/threads", params={"client": "globex"}).json()
    assert [t["id"] for t in listed] == [two_clients["globex-portal"]]
    assert listed[0]["places"] == ["globex › globex-portal"]  # noqa: RUF001 (the label uses it)

    decisions = owner.get("/decisions", params={"client": "Acme"}).json()
    assert sorted(d["project"] for d in decisions) == ["acme-app", "acme-web"]
    assert {(d["client"], d["production"]) for d in decisions} == {("Acme", False)}
    assert len(owner.get("/decisions").json()) == 3  # the badge counts everything
    assert owner.get("/audit", params={"client": "nobody"}).json() == []
