"""Where Claude runs: what a worker gets for each place, and a project's own choice."""

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from ringkeeper.claude_auth import BILLING_ENV_VARS, ClaudeAuthError, ClaudeCredential
from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import AuthMode
from ringkeeper.router.model import sdk_env, with_google_key
from ringkeeper.scrub import scrub
from ringkeeper.services import claude_place
from ringkeeper.services.claude_place import BEDROCK, FOUNDRY, GOOGLE_KEY_ENV, VERTEX, Place
from ringkeeper.vault import Secret, Vault, get_vault

KEY = "fake-bedrock-key"
GOOGLE = json.dumps({"type": "service_account", "private_key": "fake-private-key"})


@pytest.fixture
def vault(database_url: str) -> Vault:
    return get_vault()


def save(vault: Vault, key: str | None = None, **data: Any) -> None:
    with session_scope() as session:
        claude_place.save(session, vault, data, key)


def credential(vault: Vault, project: str | None = None) -> ClaudeCredential:
    with session_scope(commit=False) as session:
        return claude_place.credential(session, vault, get_settings(), project)


def test_bedrock_with_a_key(vault: Vault) -> None:
    save(vault, KEY, kind=BEDROCK, region="us-east-1", models={"opus": "us.anthropic.opus"})
    env = credential(vault).env()
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    assert env["AWS_REGION"] == "us-east-1"
    assert env["AWS_BEARER_TOKEN_BEDROCK"] == KEY
    assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "us.anthropic.opus"
    assert "ANTHROPIC_API_KEY" not in env
    assert scrub(f"key {KEY}") != f"key {KEY}"


def fake_cli(monkeypatch: pytest.MonkeyPatch, answer: str, fail: bool = False) -> list[list[str]]:
    seen: list[list[str]] = []

    def run(command: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(command)
        if fail:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, answer, "")

    monkeypatch.setattr(claude_place.shutil, "which", lambda name: f"/bin/{name}")  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.setattr(claude_place.subprocess, "run", run)
    return seen


def test_a_bedrock_profile_passes_short_lived_credentials(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    answer = json.dumps({"AccessKeyId": "AKIATEMP", "SecretAccessKey": "s", "SessionToken": "t"})
    seen = fake_cli(monkeypatch, answer)
    save(vault, kind=BEDROCK, region="us-east-1", profile="gi-prod")
    env = credential(vault).env()
    assert seen[0][1:] == [
        "configure", "export-credentials", "--profile", "gi-prod", "--format", "process",
    ]  # fmt: skip
    assert (env["AWS_ACCESS_KEY_ID"], env["AWS_SESSION_TOKEN"]) == ("AKIATEMP", "t")
    assert "AWS_PROFILE" not in env  # a worker can't reach ~/.aws anyway


def test_an_expired_profile_says_how_to_sign_in(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_cli(monkeypatch, "", fail=True)
    save(vault, kind=BEDROCK, region="us-east-1", profile="gi-prod")
    with pytest.raises(ClaudeAuthError, match="aws sso login --profile gi-prod"):
        credential(vault)


def test_the_routers_vertex_credential_carries_its_key(vault: Vault) -> None:
    save(vault, GOOGLE, kind=VERTEX, region="global", project="acme-prod")
    env = credential(vault).env()
    assert env["CLAUDE_CODE_USE_VERTEX"] == "1"
    assert env["CLOUD_ML_REGION"] == "global"
    assert env["ANTHROPIC_VERTEX_PROJECT_ID"] == "acme-prod"
    assert env[GOOGLE_KEY_ENV] == GOOGLE


def test_the_router_writes_vertexs_key_to_a_file_of_its_own(tmp_path: Path) -> None:
    env = with_google_key({GOOGLE_KEY_ENV: GOOGLE, "A": "b"}, str(tmp_path))
    assert GOOGLE_KEY_ENV not in env
    assert Path(env["GOOGLE_APPLICATION_CREDENTIALS"]).read_text(encoding="utf-8") == GOOGLE


def test_foundry_with_a_key_or_the_azure_login(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(vault, "fake-foundry-key", kind=FOUNDRY, resource="acme-ai")
    env = credential(vault).env()
    assert (env["CLAUDE_CODE_USE_FOUNDRY"], env["ANTHROPIC_FOUNDRY_RESOURCE"]) == ("1", "acme-ai")
    assert env["ANTHROPIC_FOUNDRY_API_KEY"] == "fake-foundry-key"

    fake_cli(monkeypatch, json.dumps({"accessToken": "az-token"}))
    save(vault, kind=FOUNDRY, resource="acme-ai", use_login=True)
    env = credential(vault).env()
    assert env["ANTHROPIC_FOUNDRY_AUTH_TOKEN"] == "az-token"
    assert "ANTHROPIC_FOUNDRY_API_KEY" not in env


def test_the_router_blanks_what_it_inherited_and_uses_the_place(vault: Vault) -> None:
    save(vault, KEY, kind=BEDROCK, region="us-east-1")
    env = sdk_env(credential(vault), "/tmp/x")  # noqa: S108 (a fake path)
    assert env["ANTHROPIC_API_KEY"] == ""
    assert env["CLAUDE_CODE_USE_VERTEX"] == ""
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"


def test_every_place_variable_is_a_billing_variable() -> None:
    for name in ("AWS_BEARER_TOKEN_BEDROCK", "ANTHROPIC_FOUNDRY_API_KEY", "CLOUD_ML_REGION"):
        assert name in BILLING_ENV_VARS


def test_anthropic_stays_the_default_and_a_budget_still_applies(vault: Vault) -> None:
    with session_scope(commit=False) as session:
        assert claude_place.chosen(session).kind == "anthropic"
    save(vault, KEY, kind=BEDROCK, region="us-east-1")
    assert credential(vault).mode is AuthMode.API_KEY  # billed by the token, so budgets count


def test_a_project_can_run_somewhere_else(vault: Vault) -> None:
    save(vault, KEY, kind=BEDROCK, region="us-east-1")
    save(vault, "fake-foundry-key", kind=FOUNDRY, resource="acme-ai")
    with session_scope() as session:
        claude_place.set_project(session, "acme-web", BEDROCK)
    assert "CLAUDE_CODE_USE_BEDROCK" in credential(vault, "acme-web").env()
    assert "CLAUDE_CODE_USE_FOUNDRY" in credential(vault, "globex-app").env()  # the default
    with session_scope() as session:
        claude_place.set_project(session, "acme-web", None)
    assert "CLAUDE_CODE_USE_FOUNDRY" in credential(vault, "acme-web").env()


def test_a_project_cant_pick_what_isnt_set_up(vault: Vault) -> None:
    with session_scope() as session, pytest.raises(RuleViolationError, match="Set that up first"):
        claude_place.set_project(session, "acme-web", VERTEX)


@pytest.mark.parametrize(
    ("model", "pins", "becomes"),
    [
        ("claude-sonnet-5-5", {"sonnet": "us.anthropic.sonnet"}, "us.anthropic.sonnet"),
        ("claude-opus-5-5", {"sonnet": "us.anthropic.sonnet"}, "claude-opus-5-5"),
        ("claude-haiku-5-5", {}, "claude-haiku-5-5"),
        (None, {"sonnet": "x"}, None),
    ],
)
def test_a_pinned_model_replaces_its_family(
    model: str | None, pins: dict[str, str], becomes: str | None
) -> None:
    assert claude_place.model_for(Place(BEDROCK, models=pins), model) == becomes


@pytest.mark.parametrize(
    ("data", "says"),
    [
        ({"kind": BEDROCK, "region": "not a region"}, "AWS region"),
        ({"kind": BEDROCK, "region": "us-east-1", "profile": "a;rm"}, "profile"),
        ({"kind": VERTEX, "region": "global", "project": "BAD"}, "project"),
        ({"kind": FOUNDRY, "resource": "https://x.example"}, "name alone"),
        ({"kind": "elsewhere"}, "Choose"),
        ({"kind": BEDROCK, "region": "us-east-1", "models": {"mega": "x"}}, "no mega"),
    ],
)
def test_what_goes_to_a_cli_or_an_address_is_checked(
    vault: Vault, data: dict[str, Any], says: str
) -> None:
    with pytest.raises(RuleViolationError, match=says):
        save(vault, **data)


def test_a_vertex_key_has_to_be_one(vault: Vault) -> None:
    with pytest.raises(RuleViolationError, match="service account key"):
        save(vault, "not json", kind=VERTEX, region="global", project="acme-prod")


def test_no_key_says_where_to_add_it(vault: Vault) -> None:
    save(vault, kind=BEDROCK, region="us-east-1")
    with pytest.raises(ClaudeAuthError, match="Settings > Claude"):
        credential(vault)


def test_a_key_is_kept_out_of_the_saved_settings(vault: Vault) -> None:
    save(vault, KEY, kind=BEDROCK, region="us-east-1")
    with session_scope(commit=False) as session:
        assert KEY not in json.dumps(claude_place.places(session))
    assert claude_place.stored_keys(vault) == [BEDROCK]


def test_the_live_check_uses_what_a_worker_would(
    vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    save(vault, KEY, kind=BEDROCK, region="us-east-1")
    asked: list[tuple[dict[str, str], str]] = []

    def ask(_self: Any, found: ClaudeCredential, _system: str, _prompt: str, model: str) -> None:
        asked.append((found.env(), model))

    monkeypatch.setattr("ringkeeper.router.model.AgentSdkModel.ask", ask)
    said = claude_place.check(Place(BEDROCK, region="us-east-1"), vault, get_settings())
    assert said == "It answered, using haiku."
    assert asked[0][0]["AWS_BEARER_TOKEN_BEDROCK"] == KEY


def test_a_credential_built_for_a_place_has_no_key_of_its_own() -> None:
    found = ClaudeCredential(AuthMode.API_KEY, "r", Secret("", ""), place={"A": "b"})
    assert found.env() == {"A": "b"}
