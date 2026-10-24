import pytest
from typer.testing import CliRunner, Result

from ringkeeper.cli.main import app

runner = CliRunner()


def run(*args: str) -> Result:
    return runner.invoke(app, list(args))


def ok(*args: str) -> str:
    result = run(*args)
    assert result.exit_code == 0, result.output
    return result.stdout


def fails(*args: str) -> str:
    result = run(*args)
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    return result.stderr


@pytest.fixture
def seeded(database_url: str) -> None:
    ok("project", "add", "acme-web", "--owner", "acme", "--alias", "acme,acme-portal")
    ok(
        "instance", "add", "acme-supabase-dev", "--type", "supabase", "--env", "dev",
        "--secret-ref", "file:///run/secrets/acme-supabase-dev", "--owner", "acme",
    )  # fmt: skip
    ok(
        "instance", "add", "acme-supabase-prod", "--type", "supabase", "--env", "prod",
        "--secret-ref", "aws-sm://ringkeeper/acme/supabase-prod", "--owner", "acme",
        "--base-url", "https://acme-prod.supabase.example",
    )  # fmt: skip


def test_project_and_instance_lifecycle(seeded: None) -> None:
    assert "acme-web" in ok("project", "list")
    assert "acme, acme-portal" in ok("project", "list")
    instance_list = ok("instance", "list", "--env", "prod")
    assert "acme-supabase-prod" in instance_list
    assert "acme-supabase-dev" not in instance_list

    assert "bound acme-supabase-dev to acme-web (default)" in ok(
        "bind", "acme-web", "acme-supabase-dev", "--default", "--tools", "sql.read,storage.read"
    )
    ok(
        "bind",
        "acme-web",
        "acme-supabase-prod",
        "--tools",
        "sql.read",
        "--preapprove",
        "a",
        "--preapprove",
        "b",
    )

    shown = ok("project", "show", "acme-web")
    assert "acme-supabase-dev   supabase  dev   yes      sql.read, storage.read" in shown
    assert "acme-supabase-prod  supabase  prod           sql.read" in shown
    assert "a, b" in shown

    instance = ok("instance", "show", "acme-supabase-prod")
    assert "aws-sm://ringkeeper/acme/supabase-prod" in instance
    assert "bound to:   acme-web" in instance

    ok("unbind", "acme-web", "acme-supabase-prod")
    assert "acme-supabase-prod" not in ok("project", "show", "acme-web")
    assert "removed project acme-web and 1 binding(s)" in ok("project", "remove", "acme-web")


def test_rebinding_updates(seeded: None) -> None:
    ok("bind", "acme-web", "acme-supabase-dev")
    assert "updated binding of acme-supabase-dev to acme-web (default)" in ok(
        "bind", "acme-web", "acme-supabase-dev", "--default"
    )


def test_remove_bound_instance_needs_force(seeded: None) -> None:
    ok("bind", "acme-web", "acme-supabase-dev")
    error = fails("instance", "remove", "acme-supabase-dev")
    assert "instance 'acme-supabase-dev' is still bound to: acme-web" in error
    assert "--force" in error
    assert "unbound from acme-web" in ok("instance", "remove", "acme-supabase-dev", "--force")
    assert "acme-supabase-dev" not in ok("instance", "list")


def test_prod_cannot_be_default(seeded: None) -> None:
    error = fails("bind", "acme-web", "acme-supabase-prod", "--default")
    assert "is prod and cannot be a default" in error


def test_second_default_rejected(seeded: None) -> None:
    ok(
        "instance", "add", "acme-supabase-staging", "--type", "supabase", "--env", "staging",
        "--secret-ref", "aws-sm://ringkeeper/acme/staging", "--owner", "acme",
    )  # fmt: skip
    ok("bind", "acme-web", "acme-supabase-dev", "--default")
    error = fails("bind", "acme-web", "acme-supabase-staging", "--default")
    assert "already has a default supabase instance: 'acme-supabase-dev'" in error


def test_bind_missing_names(database_url: str) -> None:
    error = fails("bind", "ghost", "nope")
    assert "error: cannot bind: project 'ghost' and instance 'nope' do not exist" in error


def test_secret_value_is_rejected_and_not_echoed(database_url: str) -> None:
    secret = "sk-live-0123456789abcdef"  # gitleaks:allow (fake value; tests rejection)
    error = fails(
        "instance", "add", "acme-x", "--type", "neon", "--env", "dev",
        "--secret-ref", secret, "--owner", "acme",
    )  # fmt: skip
    assert "secret_ref: Value error, secret_ref must be a pointer" in error
    assert secret not in error
    assert "acme-x" not in ok("instance", "list")


def test_invalid_slug(database_url: str) -> None:
    error = fails("project", "add", "Acme Web", "--owner", "acme")
    assert "name: String should match pattern" in error


def test_show_missing(database_url: str) -> None:
    assert "error: project 'ghost' does not exist" in fails("project", "show", "ghost")
    assert "error: instance 'ghost' does not exist" in fails("instance", "show", "ghost")


def test_unreachable_database_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "RINGKEEPER_DATABASE_URL", "postgresql+psycopg://someone:hunter2@127.0.0.1:1/nowhere"
    )
    monkeypatch.setenv("RINGKEEPER_DATABASE_CONNECT_TIMEOUT", "2")
    error = fails("project", "list")
    assert "cannot reach the database" in error
    assert "hunter2" not in error
