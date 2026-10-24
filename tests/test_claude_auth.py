import pytest
from typer.testing import CliRunner

from ringkeeper.claude_auth import (
    BILLING_ENV_VARS,
    CREDENTIAL_ENV,
    ClaudeAuthError,
    check_format,
    resolve,
)
from ringkeeper.cli.main import app
from ringkeeper.config import Settings
from ringkeeper.models import AuthMode
from ringkeeper.vault import get_vault

runner = CliRunner()
# Fakes, shaped like the real prefixes but nowhere near a real key's length.
API_KEY = "sk-ant-api03-fake-test-key-0001"
OAUTH_TOKEN = "sk-ant-oat01-fake-test-token-0001"


def test_default_mode_is_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RINGKEEPER_CLAUDE_AUTH_MODE", raising=False)
    assert Settings().claude_auth_mode is AuthMode.API_KEY


@pytest.mark.parametrize(
    ("mode", "env_name", "value"),
    [
        (AuthMode.API_KEY, "ANTHROPIC_API_KEY", API_KEY),
        (AuthMode.SUBSCRIPTION, "CLAUDE_CODE_OAUTH_TOKEN", OAUTH_TOKEN),
    ],
)
def test_each_mode_yields_exactly_one_credential(mode: AuthMode, env_name: str, value: str) -> None:
    settings = Settings(claude_auth_mode=mode)
    vault = get_vault(settings)
    ref = (
        settings.claude_api_key_ref if mode is AuthMode.API_KEY else settings.claude_oauth_token_ref
    )
    vault.store(ref, value)
    env = resolve(vault, settings).env()
    assert env == {env_name: value}
    # In subscription mode, nothing that would switch billing to an API key.
    others = set(BILLING_ENV_VARS) - {env_name}
    assert not others & set(env)


def test_both_credential_vars_are_billing_vars() -> None:
    assert set(CREDENTIAL_ENV.values()) <= set(BILLING_ENV_VARS)


@pytest.mark.parametrize(
    ("mode", "value", "message"),
    [
        (AuthMode.API_KEY, OAUTH_TOKEN, "looks like an OAuth token"),
        (AuthMode.SUBSCRIPTION, API_KEY, "looks like an Anthropic API key"),
        (AuthMode.API_KEY, "hunter2", "not an Anthropic API key"),
    ],
)
def test_wrong_kind_of_credential_is_rejected(mode: AuthMode, value: str, message: str) -> None:
    with pytest.raises(ClaudeAuthError, match=message) as excinfo:
        check_format(mode, value)
    assert value not in str(excinfo.value)


def test_missing_credential_points_at_login() -> None:
    with pytest.raises(ClaudeAuthError, match="run `ringkeeper auth login`"):
        resolve(get_vault(), Settings())


def test_login_stores_an_api_key_from_stdin_and_status_never_prints_it() -> None:
    result = runner.invoke(app, ["auth", "login"], input=API_KEY + "\n")
    assert result.exit_code == 0, result.output
    assert API_KEY not in result.output
    status = runner.invoke(app, ["auth", "status"])
    assert status.exit_code == 0, status.output
    assert "mode:       api_key" in status.stdout
    assert "resolves:   yes" in status.stdout
    assert API_KEY not in status.output


def test_login_refuses_an_oauth_token_as_api_key() -> None:
    result = runner.invoke(app, ["auth", "login"], input=OAUTH_TOKEN + "\n")
    assert result.exit_code == 1
    assert "looks like an OAuth token" in result.stderr
    assert OAUTH_TOKEN not in result.output


def test_subscription_is_never_offered_in_setup() -> None:
    help_text = runner.invoke(app, ["auth", "login", "--help"]).stdout
    assert "subscription" not in help_text.lower()


def test_hidden_subscription_login_does_not_switch_modes() -> None:
    result = runner.invoke(app, ["auth", "login", "--subscription"], input=OAUTH_TOKEN + "\n")
    assert result.exit_code == 0, result.output
    assert "personal use on your own Claude account" in result.stderr
    assert "Workers still use api_key mode" in result.stderr
    assert OAUTH_TOKEN not in result.output
    status = runner.invoke(app, ["auth", "status"])
    assert "mode:       api_key" in status.stdout  # unchanged


def test_status_in_subscription_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RINGKEEPER_CLAUDE_AUTH_MODE", "subscription")
    runner.invoke(app, ["auth", "login", "--subscription"], input=OAUTH_TOKEN + "\n")
    status = runner.invoke(app, ["auth", "status"])
    assert status.exit_code == 0, status.output
    assert "mode:       subscription" in status.stdout
    assert "personal use on your own Claude account" in status.stdout


def test_status_without_credential_fails() -> None:
    status = runner.invoke(app, ["auth", "status"])
    assert status.exit_code == 1
    assert "resolves:   no" in status.stdout
