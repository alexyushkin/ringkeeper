from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from ringkeeper.admin_token import TOKEN_PREFIX
from ringkeeper.api.app import create_app
from ringkeeper.cli.main import app as cli
from ringkeeper.services import registry

EXAMPLE = Path(__file__).parents[1] / "examples" / "registry.yaml"


def cli_api_token(*args: str) -> str:
    result = CliRunner().invoke(cli, ["auth", "api-token", *args])
    assert result.exit_code == 0, result.output
    return result.stdout.strip()


@pytest.fixture
def api_token(data_dir: Path) -> str:
    return cli_api_token()


@pytest.fixture
def client(database_url: str, api_token: str) -> Iterator[TestClient]:
    with TestClient(create_app(), headers={"Authorization": f"Bearer {api_token}"}) as client:
        yield client


@pytest.fixture
def anonymous(database_url: str, data_dir: Path) -> Iterator[TestClient]:
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


def test_health_needs_no_token(anonymous: TestClient) -> None:
    assert anonymous.get("/health").status_code == 200


PROTECTED = ["/projects", "/projects/acme-web", "/instances"]


@pytest.mark.parametrize("path", PROTECTED)
@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Basic abc"}, {"Authorization": f"Bearer {TOKEN_PREFIX}wrong"}],
)
def test_every_other_endpoint_needs_the_admin_token(
    anonymous: TestClient, api_token: str, path: str, headers: dict[str, str]
) -> None:
    response = anonymous.get(path, headers=headers)
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_api_fails_closed_without_a_stored_token(anonymous: TestClient) -> None:
    response = anonymous.get("/projects", headers={"Authorization": "Bearer anything"})
    assert response.status_code == 503
    assert "ringkeeper auth api-token" in response.json()["detail"]


def test_docs_and_schema_are_off(anonymous: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anonymous.get(path).status_code == 404


def test_api_token_is_stable_until_rotated(client: TestClient, api_token: str) -> None:
    assert api_token.startswith(TOKEN_PREFIX)
    assert cli_api_token() == api_token
    rotated = cli_api_token("--rotate")
    assert rotated != api_token
    assert client.get("/projects").status_code == 401  # the old one stops working at once
    ok = client.get("/projects", headers={"Authorization": f"Bearer {rotated}"})
    assert ok.status_code == 200


def test_health_reports_unreachable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_DATABASE_URL", "postgresql+psycopg://x:y@127.0.0.1:1/nowhere")
    monkeypatch.setenv("RINGKEEPER_DATABASE_CONNECT_TIMEOUT", "2")
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
        "scope": {},
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


def test_writes_need_a_signed_in_owner_too(client: TestClient, loaded: None) -> None:
    # The admin token alone reads; changes come from the web app's signed-in owner.
    assert client.post("/projects", json={}).status_code == 401
    assert client.delete("/projects/acme-web").status_code == 401
    assert client.put("/instances", json={}).status_code == 405
