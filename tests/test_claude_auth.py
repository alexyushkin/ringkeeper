import pytest
from typer.testing import CliRunner

from ringkeeper.claude_auth import (
    BILLING_ENV_VARS,
    CREDENTIAL_ENV,
    ClaudeAuthError,
    check_format,
    normalize,
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
USER_KEY = "sk-ant-usr-fake-test-key-0001"  # newer console keys start like this


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
        (AuthMode.API_KEY, OAUTH_TOKEN, "subscription token, not an API key"),
        (AuthMode.SUBSCRIPTION, API_KEY, "looks like an Anthropic API key"),
        (AuthMode.SUBSCRIPTION, USER_KEY, "looks like an Anthropic API key"),
        (AuthMode.API_KEY, "hunter2", "isn't an Anthropic API key"),
        (AuthMode.API_KEY, "sk-proj-fake-openai-key-0001", "isn't an Anthropic API key"),
    ],
)
def test_wrong_kind_of_credential_is_rejected(mode: AuthMode, value: str, message: str) -> None:
    with pytest.raises(ClaudeAuthError, match=message) as excinfo:
        check_format(mode, value)
    assert value not in str(excinfo.value)


@pytest.mark.parametrize("key", [API_KEY, USER_KEY, "sk-ant-admin01-fake-0001"])
def test_any_anthropic_key_passes_the_shape_check(key: str) -> None:
    check_format(AuthMode.API_KEY, key)  # Anthropic's answer decides (services.secrets_in)


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
    assert "subscription token, not an API key" in result.stderr
    assert OAUTH_TOKEN not in result.output


def test_login_stores_a_user_key() -> None:
    result = runner.invoke(app, ["auth", "login"], input=USER_KEY + "\r\n")
    assert result.exit_code == 0, result.output
    settings = Settings()
    assert resolve(get_vault(settings), settings).secret.reveal() == USER_KEY


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


def test_missing_subscription_credential_points_at_subscription_login() -> None:
    settings = Settings(claude_auth_mode=AuthMode.SUBSCRIPTION)
    with pytest.raises(ClaudeAuthError, match="run `ringkeeper auth login --subscription`"):
        resolve(get_vault(settings), settings)


# --- Reading pasted or piped credentials ---

WRAPPED = {
    "crlf from PowerShell": OAUTH_TOKEN + "\r\n",
    "wrapped lines": OAUTH_TOKEN[:12] + "\r\n" + OAUTH_TOKEN[12:20] + "\n" + OAUTH_TOKEN[20:],
    "spaces and tabs": "  " + OAUTH_TOKEN[:10] + " \t" + OAUTH_TOKEN[10:] + "  ",
    "utf-8 bom": "\ufeff" + OAUTH_TOKEN + "\n",
}


@pytest.mark.parametrize("raw", WRAPPED.values(), ids=WRAPPED.keys())
def test_normalize_strips_all_whitespace(raw: str) -> None:
    assert normalize(raw) == OAUTH_TOKEN


@pytest.mark.parametrize("raw", WRAPPED.values(), ids=WRAPPED.keys())
def test_subscription_login_from_stdin_stores_the_clean_token(raw: str) -> None:
    result = runner.invoke(app, ["auth", "login", "--subscription"], input=raw)
    assert result.exit_code == 0, result.output
    settings = Settings(claude_auth_mode=AuthMode.SUBSCRIPTION)
    assert resolve(get_vault(settings), settings).secret.reveal() == OAUTH_TOKEN
    assert OAUTH_TOKEN not in result.output


@pytest.mark.parametrize("flags", [[], ["--api-key"]])
def test_api_key_login_from_stdin_strips_line_breaks(flags: list[str]) -> None:
    wrapped = API_KEY[:15] + "\r\n" + API_KEY[15:] + "\r\n"
    result = runner.invoke(app, ["auth", "login", *flags], input=wrapped)
    assert result.exit_code == 0, result.output
    settings = Settings()
    assert resolve(get_vault(settings), settings).secret.reveal() == API_KEY


@pytest.mark.parametrize(
    ("flags", "value", "found"),
    [
        (["--subscription"], API_KEY, "looks like an Anthropic API key"),
        (["--subscription"], "pasted-garbage-0001", "doesn't start with sk-ant-"),
        ([], OAUTH_TOKEN, "subscription token, not an API key"),
        (["--api-key"], OAUTH_TOKEN, "subscription token, not an API key"),
        ([], "pasted-garbage-0001", "isn't an Anthropic API key: those start with sk-ant-"),
    ],
)
def test_shape_mismatch_is_clear_and_never_echoes(flags: list[str], value: str, found: str) -> None:
    result = runner.invoke(app, ["auth", "login", *flags], input=value + "\r\n")
    assert result.exit_code == 1
    assert found in result.stderr
    if "--subscription" in flags:
        assert "expected a value starting with sk-ant-oat" in result.stderr
    if value != OAUTH_TOKEN:
        assert f"({len(value)} characters after removing whitespace)" in result.stderr
    assert value not in result.output
    assert value[10:] not in result.output


@pytest.mark.parametrize("raw", ["", "\r\n", "   \t\n"])
def test_whitespace_only_input_stores_nothing(raw: str) -> None:
    result = runner.invoke(app, ["auth", "login", "--subscription"], input=raw)
    assert result.exit_code == 1
    assert "no value given" in result.stderr


def test_interactive_login_asks_once_without_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_prompt(text: str, **kwargs: object) -> str:
        calls.append({"text": text, **kwargs})
        return OAUTH_TOKEN[:20] + "\r\n" + OAUTH_TOKEN[20:]  # a wrapped paste

    monkeypatch.setattr("ringkeeper.cli.auth._interactive", lambda: True)
    monkeypatch.setattr("ringkeeper.cli.auth.typer.prompt", fake_prompt)
    result = runner.invoke(app, ["auth", "login", "--subscription"])
    assert result.exit_code == 0, result.output
    [call] = calls
    assert call["hide_input"] is True
    assert not call.get("confirmation_prompt")
    assert "Get-Clipboard -Raw" in result.stderr
    settings = Settings(claude_auth_mode=AuthMode.SUBSCRIPTION)
    assert resolve(get_vault(settings), settings).secret.reveal() == OAUTH_TOKEN
