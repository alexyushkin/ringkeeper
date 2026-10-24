from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from interlock.api.app import create_app
from interlock.services import registry

EXAMPLE = Path(__file__).parents[1] / "examples" / "registry.yaml"


@pytest.fixture
def client(database_url: str) -> Iterator[TestClient]:
    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def loaded(session: Session) -> None:
    registry.apply_registry(session, registry.load_registry_file(EXAMPLE))
    session.commit()


def assert_no_secrets(body: str) -> None:
    assert "secret_ref" not in body
    assert "aws-sm://" not in body
    assert "file://" not in body


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_health_reports_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INTERLOCK_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/nowhere")
    monkeypatch.setenv("INTERLOCK_DATABASE_CONNECT_TIMEOUT", "2")
    with TestClient(create_app()) as client:
        response = client.get("/health")
    assert response.status_code == 503
    assert response.json() == {"status": "error", "database": "unreachable"}


def test_list_projects(client: TestClient, loaded: None) -> None:
    response = client.get("/projects")
    assert response.status_code == 200
    body = response.json()
    assert [p["name"] for p in body] == ["acme-reports", "acme-web", "globex-portal"]
    assert body[1]["aliases"] == ["acme", "acme-portal"]
    assert body[1]["autonomy"] == "standard"


def test_get_project_includes_bound_instances_without_secrets(
    client: TestClient, loaded: None
) -> None:
    response = client.get("/projects/acme-web")
    assert response.status_code == 200
    assert_no_secrets(response.text)
    bound = {i["name"]: i for i in response.json()["instances"]}
    assert sorted(bound) == [
        "acme-n8n",
        "acme-supabase-dev",
        "acme-supabase-prod",
        "acme-supabase-staging",
    ]
    assert bound["acme-supabase-dev"]["is_default"] is True
    assert bound["acme-supabase-prod"] == {
        "name": "acme-supabase-prod",
        "type": "supabase",
        "env": "prod",
        "base_url": "https://acme-prod.supabase.example",
        "owner": "acme-platform",
        "is_default": False,
        "allowed_tools": ["list_tables", "describe_table", "query", "execute"],
        "preapproved_writes": [],
    }
    # Another client's instances are nowhere in this project's view.
    assert "globex" not in response.text


def test_get_missing_project(client: TestClient, database_url: str) -> None:
    response = client.get("/projects/ghost")
    assert response.status_code == 404
    assert response.json() == {"detail": "project 'ghost' does not exist"}


def test_list_instances_without_secrets(client: TestClient, loaded: None) -> None:
    response = client.get("/instances")
    assert response.status_code == 200
    assert_no_secrets(response.text)
    assert len(response.json()) == 7

    prod = client.get("/instances", params={"env": "prod", "type": "supabase"}).json()
    assert [i["name"] for i in prod] == ["acme-supabase-prod"]


def test_api_is_read_only(client: TestClient, loaded: None) -> None:
    assert client.post("/projects", json={}).status_code == 405
    assert client.delete("/projects/acme-web").status_code == 405
    assert client.put("/instances", json={}).status_code == 405
