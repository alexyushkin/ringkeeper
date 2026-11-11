"""Where Claude runs: Anthropic's own service, Amazon Bedrock, Google Vertex AI or Microsoft
Foundry. Settings > Claude picks the default, and a project can use another.

Workers and the architect hold no model credential. `worker_vars` points them at the
gateway's model proxy, with a per-task token, and the proxy attaches the real credential
(services.model_upstream). The router and the Help assistant run inside the backend with
every tool off, so they get the credential directly (`credential`).

- Bedrock: an API key, or an AWS profile's temporary credentials (the AWS CLI's own
  `configure export-credentials`).
- Vertex: a service account key.
- Foundry: an API key, or an access token from the Azure CLI's login.

A cloud's model IDs differ from Anthropic's, so each place can pin one per family, and
`model_for` swaps it in wherever a model is chosen.
"""

import json
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy.orm import Session

from ringkeeper import claude_failures
from ringkeeper.claude_auth import ClaudeAuthError, ClaudeCredential, resolve
from ringkeeper.config import Settings
from ringkeeper.errors import RingkeeperError, RuleViolationError
from ringkeeper.models import AuthMode, Setting
from ringkeeper.scrub import register
from ringkeeper.vault import Secret, Vault

ANTHROPIC, BEDROCK, VERTEX, FOUNDRY = "anthropic", "bedrock", "vertex", "foundry"
PLACES = (ANTHROPIC, BEDROCK, VERTEX, FOUNDRY)
DEFAULT_KEY = "claude.place"
PROJECT_KEY = "claude.place.project.{name}"
KEY_REF = "file://ringkeeper/claude-place/{kind}"
GOOGLE_KEY_ENV = "RINGKEEPER_GOOGLE_KEY"  # the router writes it to a file that it removes
FAMILIES = ("opus", "sonnet", "haiku")
CLI_TIMEOUT = 30
AZURE_SCOPE = "https://cognitiveservices.azure.com/.default"
REGION = re.compile(r"^[a-z0-9-]{2,30}$")
RESOURCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,62}$")
PROFILE = re.compile(r"^[A-Za-z0-9_.@-]{1,64}$")
GOOGLE_PROJECT = re.compile(r"^[a-z][a-z0-9-]{4,62}$")
MODEL = re.compile(r"^[A-Za-z0-9_.:/@\[\]-]{1,200}$")


@dataclass(frozen=True)
class Place:
    kind: str
    region: str = ""
    profile: str = ""  # Bedrock: an AWS profile on this computer, instead of a key
    project: str = ""  # Vertex: the Google Cloud project
    resource: str = ""  # Foundry: the resource's name
    use_login: bool = False  # Foundry: the Azure CLI's login, instead of a key
    models: Mapping[str, str] = field(default_factory=dict[str, str])  # family -> the cloud's ID


def _saved(session: Session, key: str) -> dict[str, Any]:
    row = session.get(Setting, key)
    return dict(row.value) if row else {}


def _keep(session: Session, key: str, value: dict[str, Any]) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def as_place(data: dict[str, Any]) -> Place:
    """A place as the form describes it."""
    return _place(data)


def _place(data: dict[str, Any]) -> Place:
    return Place(
        str(data.get("kind") or ANTHROPIC), str(data.get("region") or ""),
        str(data.get("profile") or ""), str(data.get("project") or ""),
        str(data.get("resource") or ""), bool(data.get("use_login")),
        {str(k): str(v) for k, v in cast(dict[str, Any], data.get("models") or {}).items() if v},
    )  # fmt: skip


def places(session: Session) -> dict[str, Any]:
    """Each place's settings, and which is the default. Keys stay in the vault."""
    return _saved(session, DEFAULT_KEY) or {"default": ANTHROPIC, "places": {}}


def _check(place: Place) -> None:
    """What a place needs, and values safe to pass to a CLI or an address."""
    if place.kind not in PLACES:
        raise RuleViolationError("Choose where Claude runs.")
    if unknown := sorted(set(place.models) - set(FAMILIES)):
        raise RuleViolationError(f"There's no {unknown[0]} model family.")
    if place.kind == ANTHROPIC:
        return
    for model in place.models.values():
        if not MODEL.fullmatch(model):
            raise RuleViolationError("That model ID doesn't look right.")
    if place.kind == BEDROCK:
        if not REGION.fullmatch(place.region):
            raise RuleViolationError("Give the AWS region, like us-east-1.")
        if place.profile and not PROFILE.fullmatch(place.profile):
            raise RuleViolationError("That AWS profile name doesn't look right.")
    elif place.kind == VERTEX:
        if not GOOGLE_PROJECT.fullmatch(place.project):
            raise RuleViolationError("Give the Google Cloud project's ID.")
        if not REGION.fullmatch(place.region):
            raise RuleViolationError("Give the region, like global or us-east5.")
    elif not RESOURCE.fullmatch(place.resource):
        raise RuleViolationError("Give the Foundry resource's name alone, not its address.")


def _check_key(kind: str, key: str) -> None:
    if kind != VERTEX:
        return
    try:
        parsed: Any = json.loads(key)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict) or not cast(dict[str, Any], parsed).get("private_key"):
        raise RuleViolationError("That isn't a Google service account key file.")


def save(
    session: Session,
    vault: Vault,
    data: dict[str, Any],
    key: str | None = None,
    make_default: bool = True,
) -> None:
    """One place's settings, and (`key`) its secret. Only what's stored here is shown back."""
    place = _place(data)
    _check(place)
    if key:
        _check_key(place.kind, key)
        vault.store(KEY_REF.format(kind=place.kind), key)
    saved = places(session)
    kept = dict(saved.get("places") or {})
    kept[place.kind] = {k: v for k, v in data.items() if k != "key"}
    default = place.kind if make_default else saved.get("default", ANTHROPIC)
    _keep(session, DEFAULT_KEY, {"default": default, "places": kept})
    claude_failures.clear(session)  # another place may not have the limit


def set_project(session: Session, project: str, kind: str | None) -> None:
    """Which place this project uses. None: the default."""
    if kind is not None and kind not in PLACES:
        raise RuleViolationError("Choose where Claude runs.")
    key = PROJECT_KEY.format(name=project)
    claude_failures.clear(session)  # another place may not have the limit
    if kind is None:
        row = session.get(Setting, key)
        if row is not None:
            session.delete(row)
    else:
        if kind != ANTHROPIC and kind not in dict(places(session).get("places") or {}):
            raise RuleViolationError("Set that up first, in Settings > Claude.")
        _keep(session, key, {"kind": kind})


def project_choice(session: Session, project: str) -> str | None:
    return _saved(session, PROJECT_KEY.format(name=project)).get("kind")


def chosen(session: Session, project: str | None = None) -> Place:
    """The place this work runs in: the project's, or the default."""
    saved = places(session)
    kind = (project_choice(session, project) if project else None) or saved.get("default")
    data = dict(cast(dict[str, Any], saved.get("places") or {}).get(str(kind)) or {})
    return _place({**data, "kind": kind or ANTHROPIC})


def model_for(place: Place, model: str | None) -> str | None:
    """The cloud's own ID for a model, by its family, when that family is pinned."""
    if model is None:
        return None
    return next((pin for family, pin in place.models.items() if family in model.lower()), model)


def _run(command: list[str], what: str, fix: str) -> str:
    found = shutil.which(command[0])
    if found is None:
        raise ClaudeAuthError(f"{what} needs {command[0]} on this computer. {fix}")
    try:
        done = subprocess.run(  # noqa: S603 (fixed arguments, checked values)
            [found, *command[1:]], capture_output=True, text=True, timeout=CLI_TIMEOUT, check=True
        )
    except (OSError, subprocess.SubprocessError):
        raise ClaudeAuthError(f"{what} didn't work. {fix}") from None
    return done.stdout


def aws_login(profile: str) -> dict[str, str]:
    out = _run(
        ["aws", "configure", "export-credentials", "--profile", profile, "--format", "process"],
        f"The AWS profile {profile}", f"Sign in again: aws sso login --profile {profile}",
    )  # fmt: skip
    data = cast(dict[str, Any], json.loads(out))
    env = {
        "AWS_ACCESS_KEY_ID": str(data["AccessKeyId"]),
        "AWS_SECRET_ACCESS_KEY": str(data["SecretAccessKey"]),
    }
    if data.get("SessionToken"):
        env["AWS_SESSION_TOKEN"] = str(data["SessionToken"])
    return env


def azure_login() -> dict[str, str]:
    out = _run(
        ["az", "account", "get-access-token", "--scope", AZURE_SCOPE, "--output", "json"],
        "The Azure CLI login", "Sign in again: az login",
    )  # fmt: skip
    return {"ANTHROPIC_FOUNDRY_AUTH_TOKEN": str(json.loads(out)["accessToken"])}


def stored_key(vault: Vault, kind: str, what: str) -> str:
    try:
        return vault.resolve(KEY_REF.format(kind=kind)).reveal()
    except RingkeeperError:
        raise ClaudeAuthError(f"No {what} is stored. Add it in Settings > Claude.") from None


def _pins(place: Place) -> dict[str, str]:
    return {f"ANTHROPIC_DEFAULT_{k.upper()}_MODEL": v for k, v in place.models.items()}


def place_env(place: Place, vault: Vault) -> dict[str, str]:
    """The variables that send Claude Code to this place, its credential included."""
    if place.kind == BEDROCK:
        env = {"CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": place.region}
        if place.profile:
            env |= aws_login(place.profile)
        else:
            env["AWS_BEARER_TOKEN_BEDROCK"] = stored_key(vault, BEDROCK, "Bedrock API key")
    elif place.kind == VERTEX:
        env = {
            "CLAUDE_CODE_USE_VERTEX": "1", "CLOUD_ML_REGION": place.region,
            "ANTHROPIC_VERTEX_PROJECT_ID": place.project,
            GOOGLE_KEY_ENV: stored_key(vault, VERTEX, "Google service account key"),
        }  # fmt: skip
    else:
        env = {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_RESOURCE": place.resource}
        if place.use_login:
            env |= azure_login()
        else:
            env["ANTHROPIC_FOUNDRY_API_KEY"] = stored_key(vault, FOUNDRY, "Foundry API key")
    return env | _pins(place)


def worker_vars(place: Place, gateway: str, token: str) -> dict[str, str]:
    """What a worker gets: where the model proxy is, and its own task's token. No credential."""
    base = f"{gateway.rstrip('/')}/model/{place.kind}"
    if place.kind == ANTHROPIC:
        return {"ANTHROPIC_BASE_URL": base, "ANTHROPIC_AUTH_TOKEN": token}
    if place.kind == BEDROCK:
        env = {
            "CLAUDE_CODE_USE_BEDROCK": "1", "AWS_REGION": place.region,
            "ANTHROPIC_BEDROCK_BASE_URL": base, "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1",
            "ANTHROPIC_AUTH_TOKEN": token,
        }  # fmt: skip
    elif place.kind == VERTEX:
        env = {
            "CLAUDE_CODE_USE_VERTEX": "1", "CLOUD_ML_REGION": place.region,
            "ANTHROPIC_VERTEX_PROJECT_ID": place.project, "ANTHROPIC_VERTEX_BASE_URL": base,
            "CLAUDE_CODE_SKIP_VERTEX_AUTH": "1", "ANTHROPIC_AUTH_TOKEN": token,
        }  # fmt: skip
    else:
        env = {
            "CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_BASE_URL": base,
            "ANTHROPIC_FOUNDRY_AUTH_TOKEN": token,
        }  # fmt: skip
    return env | _pins(place)


SECRET_VARS = (
    "AWS_BEARER_TOKEN_BEDROCK", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "ANTHROPIC_FOUNDRY_API_KEY", "ANTHROPIC_FOUNDRY_AUTH_TOKEN", GOOGLE_KEY_ENV,
)  # fmt: skip


def credential(
    session: Session, vault: Vault, settings: Settings, project: str | None = None
) -> ClaudeCredential:
    """What this work runs with. On Anthropic: the key or subscription, as before. In a cloud:
    its variables, in the API key's place (it bills by the token, so budgets apply)."""
    place = chosen(session, project)
    if place.kind == ANTHROPIC:
        return resolve(vault, settings)
    env = place_env(place, vault)
    register(*(env[name] for name in SECRET_VARS if name in env))
    return ClaudeCredential(
        AuthMode.API_KEY, KEY_REF.format(kind=place.kind), Secret("", ""), place=env
    )


def is_ready(session: Session, vault: Vault) -> bool:
    """Whether the default place can run, without touching a CLI."""
    place = chosen(session)
    if place.kind == ANTHROPIC or place.profile or place.use_login:
        return place.kind != ANTHROPIC
    try:
        vault.resolve(KEY_REF.format(kind=place.kind))
    except RingkeeperError:
        return False
    return True


def stored_keys(vault: Vault) -> list[str]:
    found: list[str] = []
    for kind in (BEDROCK, VERTEX, FOUNDRY):
        try:
            vault.resolve(KEY_REF.format(kind=kind))
            found.append(kind)
        except RingkeeperError:
            continue
    return found


def check(place: Place, vault: Vault, settings: Settings) -> str:
    """Live check: one tiny request, the way a worker makes it, so it proves the login, the
    region and the model together. It's billed like any other request, by a few tokens."""
    _check(place)
    if place.kind == ANTHROPIC:
        raise RuleViolationError("Anthropic's key is checked when it's saved.")
    from ringkeeper.router.model import AgentSdkModel, RouterModelError  # noqa: PLC0415 (cycle)

    env = place_env(place, vault)
    register(*(env[name] for name in SECRET_VARS if name in env))
    found = ClaudeCredential(AuthMode.API_KEY, "", Secret("", ""), place=env)
    model = place.models.get("haiku") or "haiku"
    try:
        AgentSdkModel(vault, settings).ask(found, "Reply with the word OK.", "Hi", model)
    except (RouterModelError, OSError, RuntimeError) as error:
        raise ClaudeAuthError(
            "It didn't answer. Check the login, the region, and that Claude is enabled there "
            f"({type(error).__name__})."
        ) from None
    return f"It answered, using {model}."
