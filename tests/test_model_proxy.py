"""The model proxy: a worker holds no model credential, and every call goes through here."""

import base64
import json
import struct
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.testclient import TestClient

from ringkeeper.config import get_settings
from ringkeeper.db import session_scope
from ringkeeper.gateway import model_proxy
from ringkeeper.models import AuthMode
from ringkeeper.services import (
    claude_place,
    model_upstream,
    model_usage,
    revocations,
    tasks,
)
from ringkeeper.services.claude_place import BEDROCK, FOUNDRY, VERTEX, Place
from ringkeeper.tokens import TokenRole, issue_token, model_token_id
from ringkeeper.vault import Vault, get_vault
from ringkeeper.workers.env import worker_env
from tests.helpers import make_project

API_KEY = "sk-ant-api03-fakefakefakefake"
OAUTH = "sk-ant-oat01-fakefakefakefake"
BEDROCK_KEY = "fake-bedrock-key"
FOUNDRY_KEY = "fake-foundry-key"
MESSAGE: dict[str, Any] = {"model": "claude-sonnet-5-5", "max_tokens": 1, "messages": []}
USAGE = {"input_tokens": 11, "output_tokens": 7, "cache_read_input_tokens": 3,
         "cache_creation_input_tokens": 2}  # fmt: skip
SSE = (
    b'event: message_start\ndata: {"type":"message_start","message":{"model":"claude-sonnet-5-5",'
    b'"usage":{"input_tokens":11,"output_tokens":1,"cache_read_input_tokens":3,'
    b'"cache_creation_input_tokens":2}}}\n\n'
    b'event: content_block_delta\ndata: {"type":"content_block_delta"}\n\n'
    b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":7}}\n\n'
)


def google_key() -> str:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()  # fmt: skip
    return json.dumps({"client_email": "a@b.example", "private_key": pem})


class Upstreams:
    """Stands in for every model service, and keeps what it was sent."""

    def __init__(self) -> None:
        self.seen: list[httpx2.Request] = []
        self.answer: httpx2.Response | None = None

    async def handle(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        self.seen.append(request)
        if self.answer is not None:
            return self.answer
        return httpx2.Response(200, json={"model": "claude-sonnet-5-5", "usage": USAGE})

    def google(self, request: httpx2.Request) -> httpx2.Response:
        assert b"assertion=" in request.content
        return httpx2.Response(200, json={"access_token": "ya29.fake", "expires_in": 3600})


@pytest.fixture
def world(database_url: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[Upstreams]:
    fake = Upstreams()
    monkeypatch.setattr(model_proxy, "transport", httpx2.MockTransport(fake.handle))
    monkeypatch.setattr(model_upstream, "transport", httpx2.MockTransport(fake.google))
    model_upstream.forget()
    settings = get_settings()
    get_vault(settings).store(settings.claude_api_key_ref, API_KEY)
    with session_scope() as session:
        make_project(session, "acme-web")
    yield fake
    model_upstream.forget()


@pytest.fixture
def vault(world: Upstreams) -> Vault:
    return get_vault()


@pytest.fixture
def client(vault: Vault) -> TestClient:
    app = Starlette(routes=[model_proxy.make_route(vault)])
    return TestClient(app)


def session_row() -> str:
    with session_scope() as session:
        task = tasks.create_task(session, get_settings(), project="acme-web", prompt="go")
        session.flush()
        return str(task.sessions[0].id)


def token(role: TokenRole = TokenRole.MODEL, session_id: str | None = None) -> str:
    made, _ = issue_token(
        "acme-web", timedelta(minutes=5), role=role, session_id=session_id or session_row()
    )
    return made


def headers(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}", "anthropic-version": "2023-06-01"}


def save(vault: Vault, key: str | None = None, **data: Any) -> None:
    with session_scope() as session:
        claude_place.save(session, vault, data, key)


def test_the_real_key_goes_on_on_the_way_out_and_the_token_stops_here(
    client: TestClient, world: Upstreams
) -> None:
    sent = token()
    found = client.post("/model/anthropic/v1/messages", json=MESSAGE, headers=headers(sent))
    assert found.status_code == 200
    out = world.seen[0]
    assert str(out.url) == "https://api.anthropic.com/v1/messages"
    assert out.headers["x-api-key"] == API_KEY
    assert sent not in str(dict(out.headers))
    assert "authorization" not in out.headers


def test_a_subscription_goes_as_its_own_bearer(
    client: TestClient, world: Upstreams, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = get_settings()
    get_vault(settings).store(settings.claude_oauth_token_ref, OAUTH)
    monkeypatch.setattr(settings, "claude_auth_mode", AuthMode.SUBSCRIPTION)
    monkeypatch.setattr(model_proxy, "current_settings", lambda _s: settings)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    given = {**headers(token()), "anthropic-beta": "fine-grained-tool-streaming-2025-05-14"}
    client.post("/model/anthropic/v1/messages", json=MESSAGE, headers=given)
    out = world.seen[0]
    assert out.headers["authorization"] == f"Bearer {OAUTH}"
    assert "x-api-key" not in out.headers
    betas = out.headers["anthropic-beta"].split(",")
    assert {"oauth-2025-04-20", "fine-grained-tool-streaming-2025-05-14"} <= set(betas)


def test_no_token_or_the_wrong_kind_is_refused(client: TestClient, world: Upstreams) -> None:
    path = "/model/anthropic/v1/messages"
    assert client.post(path, json=MESSAGE).status_code == 401
    worker = headers(token(TokenRole.WORKER))
    assert client.post(path, json=MESSAGE, headers=worker).status_code == 401
    assert client.post(path, json=MESSAGE, headers=headers("not-a-token")).status_code == 401
    assert not world.seen


def test_a_token_ends_with_its_session(client: TestClient, world: Upstreams) -> None:
    made, claims = issue_token(
        "acme-web", timedelta(minutes=5), role=TokenRole.MODEL, session_id=session_row(),
        token_id=model_token_id("abc"),
    )  # fmt: skip
    with session_scope() as session:
        revocations.revoke(session, claims.token_id, reason="ended", by="test")
    found = client.post("/model/anthropic/v1/messages", json=MESSAGE, headers=headers(made))
    assert found.status_code == 401
    assert not world.seen


@pytest.mark.parametrize(
    "path",
    ["v1/files", "v1/organizations/x", "api/oauth/profile", "v1/messages/batches", "v1/complete"],
)
def test_only_what_a_model_call_uses_is_open(
    client: TestClient, world: Upstreams, path: str
) -> None:
    found = client.post(f"/model/anthropic/{path}", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 404
    assert not world.seen


def test_a_project_only_reaches_its_own_place(client: TestClient, world: Upstreams) -> None:
    found = client.post("/model/bedrock/model/x/invoke", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 404


def test_bedrock_with_a_key(client: TestClient, world: Upstreams, vault: Vault) -> None:
    save(vault, BEDROCK_KEY, kind=BEDROCK, region="us-east-1")
    path = "/model/us.anthropic.claude-haiku-4-5-20251001-v1%3A0/invoke"
    found = client.post(f"/model/bedrock{path}", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 200
    out = world.seen[0]
    assert out.url.host == "bedrock-runtime.us-east-1.amazonaws.com"
    assert out.url.raw_path.decode() == path
    assert out.headers["authorization"] == f"Bearer {BEDROCK_KEY}"


def test_bedrock_with_a_profile_is_signed(
    client: TestClient, world: Upstreams, vault: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def login(_profile: str) -> dict[str, str]:
        return {"AWS_ACCESS_KEY_ID": "AKIATEMP", "AWS_SECRET_ACCESS_KEY": "s",
                "AWS_SESSION_TOKEN": "tok"}  # fmt: skip

    monkeypatch.setattr(claude_place, "aws_login", login)
    save(vault, kind=BEDROCK, region="us-east-1", profile="gi-prod")
    client.post("/model/bedrock/model/m/invoke", json=MESSAGE, headers=headers(token()))
    out = world.seen[0]
    assert out.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIATEMP/")
    assert "/us-east-1/bedrock/aws4_request" in out.headers["authorization"]
    assert out.headers["x-amz-security-token"] == "tok"


def test_vertex_gets_an_access_token_and_stays_in_its_project(
    client: TestClient, world: Upstreams, vault: Vault
) -> None:
    save(vault, google_key(), kind=VERTEX, region="global", project="acme-prod")
    path = "v1/projects/acme-prod/locations/global/publishers/anthropic/models/m:streamRawPredict"
    found = client.post(f"/model/vertex/{path}", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 200
    assert world.seen[0].url.host == "aiplatform.googleapis.com"
    assert world.seen[0].headers["authorization"] == "Bearer ya29.fake"
    other = path.replace("acme-prod", "globex-prod")
    refused = client.post(f"/model/vertex/{other}", json=MESSAGE, headers=headers(token()))
    assert refused.status_code == 404


@pytest.mark.parametrize(
    ("region", "host"),
    [
        ("global", "aiplatform.googleapis.com"),
        ("eu", "aiplatform.eu.rep.googleapis.com"),
        ("us-east5", "us-east5-aiplatform.googleapis.com"),
    ],
)
def test_vertexs_address_follows_the_region(region: str, host: str) -> None:
    tail = f"v1/projects/p/locations/{region}/publishers/anthropic/models/m:rawPredict"
    assert model_upstream._vertex_host(tail) == host  # pyright: ignore[reportPrivateUsage]


def test_foundry_with_a_key(client: TestClient, world: Upstreams, vault: Vault) -> None:
    save(vault, FOUNDRY_KEY, kind=FOUNDRY, resource="acme-ai")
    client.post("/model/foundry/v1/messages", json=MESSAGE, headers=headers(token()))
    out = world.seen[0]
    assert str(out.url) == "https://acme-ai.services.ai.azure.com/anthropic/v1/messages"
    assert out.headers["x-api-key"] == FOUNDRY_KEY


def test_a_project_can_use_another_place(
    client: TestClient, world: Upstreams, vault: Vault
) -> None:
    save(vault, FOUNDRY_KEY, kind=FOUNDRY, resource="acme-ai")
    save(vault, BEDROCK_KEY, kind=BEDROCK, region="us-east-1")
    with session_scope() as session:
        claude_place.set_project(session, "acme-web", FOUNDRY)
    found = client.post("/model/foundry/v1/messages", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 200
    assert world.seen[0].url.host == "acme-ai.services.ai.azure.com"


def tokens_of(session_id: str) -> dict[str, int]:
    import uuid  # noqa: PLC0415

    with session_scope(commit=False) as session:
        return model_usage.totals(session, [uuid.UUID(session_id)])


def test_a_plain_answers_tokens_are_counted(client: TestClient, world: Upstreams) -> None:
    row = session_row()
    made = token(session_id=row)
    client.post("/model/anthropic/v1/messages", json=MESSAGE, headers=headers(made))
    assert tokens_of(row) == {
        "input": 11, "output": 7, "cache_read": 3, "cache_write": 2, "requests": 1,
    }  # fmt: skip


def test_a_streams_tokens_are_counted_and_it_comes_back_whole(
    client: TestClient, world: Upstreams
) -> None:
    world.answer = httpx2.Response(200, content=SSE, headers={"content-type": "text/event-stream"})
    row = session_row()
    found = client.post(
        "/model/anthropic/v1/messages", json=MESSAGE, headers=headers(token(session_id=row))
    )
    assert found.content == SSE
    assert found.headers["content-type"].startswith("text/event-stream")
    usage = tokens_of(row)
    assert (usage["input"], usage["output"]) == (11, 7)


def frame(event: dict[str, Any]) -> bytes:
    inner = json.dumps({"bytes": base64.b64encode(json.dumps(event).encode()).decode()}).encode()
    total = 12 + len(inner) + 4
    return struct.pack(">III", total, 0, 0) + inner + b"\0\0\0\0"


def test_bedrocks_binary_stream_is_counted(world: Upstreams) -> None:
    start = {"type": "message_start", "message": {"model": "m", "usage": {"input_tokens": 5}}}
    counter = model_proxy.Counter("bedrock")
    stream = frame(start) + frame({"type": "message_delta", "usage": {"output_tokens": 9}})
    for cut in range(0, len(stream), 7):  # arrives in pieces, not on frame edges
        counter.feed(stream[cut : cut + 7])
    counter.finish()
    assert (counter.tokens["input"], counter.tokens["output"], counter.model) == (5, 9, "m")


def test_an_upstream_error_comes_back_as_it_is(client: TestClient, world: Upstreams) -> None:
    world.answer = httpx2.Response(429, json={"type": "error"}, headers={"retry-after": "7"})
    found = client.post("/model/anthropic/v1/messages", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 429
    assert found.headers["retry-after"] == "7"


def test_nothing_set_up_says_so(client: TestClient, world: Upstreams, vault: Vault) -> None:
    save(vault, kind=BEDROCK, region="us-east-1")
    found = client.post("/model/bedrock/model/m/invoke", json=MESSAGE, headers=headers(token()))
    assert found.status_code == 503
    assert "Settings > Claude" in found.text


# --- What a worker can read ---

SECRETS = (API_KEY, OAUTH, BEDROCK_KEY, FOUNDRY_KEY, "private_key", "BEGIN PRIVATE KEY")
CREDENTIAL_VARS = (
    "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID", "ANTHROPIC_FOUNDRY_API_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS", "RINGKEEPER_GOOGLE_KEY",
)  # fmt: skip


@pytest.mark.parametrize(
    "place",
    [
        Place("anthropic"),
        Place(BEDROCK, region="us-east-1"),
        Place(BEDROCK, region="us-east-1", profile="gi-prod"),
        Place(VERTEX, region="global", project="acme-prod"),
        Place(FOUNDRY, resource="acme-ai"),
        Place(FOUNDRY, resource="acme-ai", use_login=True),
    ],
    ids=["anthropic", "bedrock-key", "bedrock-profile", "vertex", "foundry-key", "foundry-login"],
)
def test_a_workers_environment_holds_no_credential(place: Place) -> None:
    env = worker_env(
        claude_place.worker_vars(place, "http://gateway:8080", "model-token"), {"a": 1}, "gw"
    )
    assert not [name for name in CREDENTIAL_VARS if name in env]
    assert not [s for s in SECRETS if s in json.dumps(env)]
    assert env.get("ANTHROPIC_AUTH_TOKEN", env.get("ANTHROPIC_FOUNDRY_AUTH_TOKEN")) == "model-token"


def test_every_place_goes_through_the_proxy() -> None:
    gateway = "http://gateway:8080"
    for place, name in (
        (Place("anthropic"), "ANTHROPIC_BASE_URL"),
        (Place(BEDROCK, region="r"), "ANTHROPIC_BEDROCK_BASE_URL"),
        (Place(VERTEX, region="r", project="p"), "ANTHROPIC_VERTEX_BASE_URL"),
        (Place(FOUNDRY, resource="x"), "ANTHROPIC_FOUNDRY_BASE_URL"),
    ):
        env = claude_place.worker_vars(place, gateway, "t")
        assert env[name] == f"{gateway}/model/{place.kind}"


def test_a_model_token_opens_nothing_else(world: Upstreams) -> None:
    from ringkeeper.gateway.app import WorkerTokenAuth  # noqa: PLC0415

    async def reached(_scope: Any, _receive: Any, _send: Any) -> None:
        raise AssertionError("a model token reached an MCP endpoint")

    http = TestClient(WorkerTokenAuth(reached, None))
    found = http.post("/", headers=headers(token()))
    assert found.status_code == 401
    assert "only reach the model" in found.text
