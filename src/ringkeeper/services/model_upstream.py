"""Where a worker's model request really goes, and the credential that goes with it.

A worker holds no model credential. It talks to the gateway's model proxy
(gateway.model_proxy) with a per-task token, and this module, on the gateway's side, says
where that request goes and how it signs in: an API key, the subscription token, or a cloud's
own sign-in. Only the routes a model call uses are open, so the token can't reach anything
else an account offers.

Each cloud keeps its own request format: Claude Code speaks it, and the proxy only signs and
forwards, so nothing is translated. A cloud's short-lived credential is kept in memory here
until shortly before it ends.
"""

import datetime
import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote

import httpx2
import jwt
from sqlalchemy.orm import Session

from ringkeeper.adapters.storage import sign_v4
from ringkeeper.claude_auth import WORKSPACE_HEADER, ClaudeAuthError, resolve
from ringkeeper.config import Settings
from ringkeeper.models import AuthMode
from ringkeeper.services import claude_place
from ringkeeper.services.claude_place import ANTHROPIC, BEDROCK, FOUNDRY, VERTEX, Place
from ringkeeper.vault import Vault

ANTHROPIC_URL = "https://api.anthropic.com"
GOOGLE_SCOPE = "https://www.googleapis.com/auth/cloud-platform"
OAUTH_BETAS = ("oauth-2025-04-20", "claude-code-20250219")
MARGIN_SECONDS = 300
AWS_LIFETIME_SECONDS = 900  # when the CLI doesn't say
TOKEN_TIMEOUT = 20.0
# What a model call needs, and nothing else an account offers.
PATHS = {
    ANTHROPIC: re.compile(r"^v1/messages(/count_tokens)?$|^v1/models(/[A-Za-z0-9._:-]+)?$"),
    BEDROCK: re.compile(r"^model/[^/]+/(invoke|invoke-with-response-stream|count-tokens)$"),
    FOUNDRY: re.compile(r"^v1/messages(/count_tokens)?$"),
}
VERTEX_PATH = r"^v1/projects/{project}/locations/[a-z0-9-]+/publishers/anthropic/models/[^/:]+:(rawPredict|streamRawPredict|countTokens)$"  # noqa: E501
# Tests swap in httpx2.MockTransport; production talks to Google.
transport: httpx2.BaseTransport | None = None
_cache: dict[str, tuple[Any, float]] = {}
_lock = threading.Lock()


class UpstreamError(Exception):
    """The model can't be reached with what's set up. The message is for the task's log."""


@dataclass(frozen=True)
class Upstream:
    kind: str
    url: str  # the whole address of this request
    headers: dict[str, str]  # the credential, and anything it needs alongside
    # Signs with the request's exact body, when the credential has to (AWS).
    sign: Callable[[str, str, bytes], dict[str, str]] | None = None


def _remembered(key: str, make: Callable[[], tuple[Any, float]]) -> Any:
    with _lock:
        found = _cache.get(key)
        if found is not None and time.time() < found[1] - MARGIN_SECONDS:
            return found[0]
    value, ends = make()
    with _lock:
        _cache[key] = (value, ends)
    return value


def forget() -> None:
    """Drops what's remembered (a place changed, or a test ended)."""
    with _lock:
        _cache.clear()


def _anthropic(vault: Vault, settings: Settings, tail: str, query: str) -> Upstream:
    credential = resolve(vault, settings)
    headers: dict[str, str] = {}
    if credential.mode is AuthMode.API_KEY:
        headers["x-api-key"] = credential.secret.reveal()
        if credential.workspace:
            headers[WORKSPACE_HEADER] = credential.workspace
    else:
        headers["Authorization"] = f"Bearer {credential.secret.reveal()}"
        headers["anthropic-beta"] = ",".join(OAUTH_BETAS)
    base = (settings.worker_anthropic_base_url or ANTHROPIC_URL).rstrip("/")
    return Upstream(ANTHROPIC, f"{base}/{tail}{query}", headers)


def _aws_keys(place: Place) -> dict[str, str]:
    def make() -> tuple[dict[str, str], float]:
        keys = claude_place.aws_login(place.profile)
        return keys, time.time() + AWS_LIFETIME_SECONDS

    return cast(dict[str, str], _remembered(f"aws:{place.profile}", make))


def _bedrock(place: Place, vault: Vault, tail: str, query: str) -> Upstream:
    url = f"https://bedrock-runtime.{place.region}.amazonaws.com/{tail}{query}"
    if not place.profile:
        key = claude_place.stored_key(vault, BEDROCK, "Bedrock API key")
        return Upstream(BEDROCK, url, {"Authorization": f"Bearer {key}"})
    keys = _aws_keys(place)

    def sign(method: str, address: str, body: bytes) -> dict[str, str]:
        now = datetime.datetime.now(datetime.UTC)
        return sign_v4(
            keys["AWS_ACCESS_KEY_ID"], keys["AWS_SECRET_ACCESS_KEY"], place.region, "bedrock",
            method, address, body, now, keys.get("AWS_SESSION_TOKEN"),
        )  # fmt: skip

    return Upstream(BEDROCK, url, {}, sign)


def _google_token(key: str) -> str:
    """An access token for a service account: a signed request, as Google's docs describe."""

    def make() -> tuple[str, float]:
        info = cast(dict[str, Any], json.loads(key))
        issued = int(time.time())
        audience = str(info.get("token_uri") or "https://oauth2.googleapis.com/token")
        claim = {
            "iss": info["client_email"], "scope": GOOGLE_SCOPE, "aud": audience,
            "iat": issued, "exp": issued + 3600,
        }  # fmt: skip
        assertion = jwt.encode(claim, str(info["private_key"]), algorithm="RS256")
        form = {"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}
        with httpx2.Client(timeout=TOKEN_TIMEOUT, transport=transport) as http:
            try:
                answer = http.post(audience, data=form)
                found = cast(dict[str, Any], answer.json())
            except (httpx2.HTTPError, ValueError):
                raise UpstreamError("Google didn't answer for a token") from None
        if answer.status_code >= 400 or not found.get("access_token"):
            raise UpstreamError("Google refused the service account key")
        return str(found["access_token"]), issued + float(found.get("expires_in") or 3600)

    digest = key[-32:]
    return cast(str, _remembered(f"google:{digest}", make))


def _vertex_host(tail: str) -> str:
    region = tail.split("/locations/", 1)[1].split("/", 1)[0]
    if region == "global":
        return "aiplatform.googleapis.com"
    if region in ("us", "eu"):
        return f"aiplatform.{region}.rep.googleapis.com"
    return f"{region}-aiplatform.googleapis.com"


def _vertex(place: Place, vault: Vault, tail: str, query: str) -> Upstream:
    token = _google_token(claude_place.stored_key(vault, VERTEX, "Google service account key"))
    url = f"https://{_vertex_host(tail)}/{tail}{query}"
    return Upstream(VERTEX, url, {"Authorization": f"Bearer {token}"})


def _foundry(place: Place, vault: Vault, tail: str, query: str) -> Upstream:
    url = f"https://{place.resource}.services.ai.azure.com/anthropic/{tail}{query}"
    if place.use_login:

        def make() -> tuple[str, float]:
            found = claude_place.azure_login()["ANTHROPIC_FOUNDRY_AUTH_TOKEN"]
            return found, time.time() + AWS_LIFETIME_SECONDS

        token = cast(str, _remembered("azure", make))
        return Upstream(FOUNDRY, url, {"Authorization": f"Bearer {token}"})
    key = claude_place.stored_key(vault, FOUNDRY, "Foundry API key")
    return Upstream(FOUNDRY, url, {"x-api-key": key})


def allowed(place: Place, kind: str, tail: str) -> bool:
    """Whether this route is open: the project's own place, and only what a model call uses.
    On Vertex that includes the project, so a token can't reach another one."""
    if kind != place.kind:
        return False
    if kind == VERTEX:
        return re.fullmatch(VERTEX_PATH.format(project=re.escape(place.project)), tail) is not None
    return PATHS[kind].fullmatch(tail) is not None


def upstream_for(
    session: Session, vault: Vault, settings: Settings, project: str, kind: str, tail: str,
    query: str,
) -> Upstream | None:  # fmt: skip
    """Where this request goes, or None if that route isn't open to this project."""
    place = claude_place.chosen(session, project)
    if not allowed(place, kind, tail):
        return None
    try:
        if place.kind == ANTHROPIC:
            return _anthropic(vault, settings, tail, query)
        if place.kind == BEDROCK:
            return _bedrock(place, vault, quote(tail, safe="/%:-_.~"), query)
        if place.kind == VERTEX:
            return _vertex(place, vault, tail, query)
        return _foundry(place, vault, tail, query)
    except ClaudeAuthError as error:
        raise UpstreamError(str(error)) from None
