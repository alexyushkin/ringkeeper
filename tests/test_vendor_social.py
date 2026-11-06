"""Bluesky, Facebook Pages, Instagram and Threads, against a fake of each: what runs, what
waits, what needs the name typed, which token goes where, and Meta's sign-in."""

import json
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest

from ringkeeper import destructive, remote
from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target, ToolError, ToolKind
from ringkeeper.adapters.vendor_spec import FINAL, HELD, READ
from ringkeeper.adapters.vendors import social
from ringkeeper.config import Settings, get_settings
from ringkeeper.db import session_scope
from ringkeeper.errors import RuleViolationError
from ringkeeper.models import Env, InstanceType
from ringkeeper.services import developer_apps, instances
from ringkeeper.services.developer_apps import SignInError
from ringkeeper.vault import Secret, get_vault

pytestmark = pytest.mark.anyio
PAGES: list[dict[str, Any]] = [
    {"id": "111", "name": "General Informatics", "token": "page-token-1", "instagram": None},
    {"id": "222", "name": "Second Page", "token": "page-token-2",
     "instagram": {"id": "901", "username": "second"}},
]  # fmt: skip
META = {"vendor": "facebook", "access_token": "user-token", "pages": PAGES}
BLUESKY = {"vendor": "bluesky", "repo": "me.bsky.social", "app_password": "abcd-efgh-ijkl-mnop"}


class Api:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url.path.endswith("createSession"):
            return httpx2.Response(200, json={"accessJwt": "bsky-session"})
        return httpx2.Response(200, json={"id": "made-1", "uri": "at://x"})

    @property
    def last(self) -> httpx2.Request:
        return self.requests[-1]


def form(request: httpx2.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.fixture
def api() -> Iterator[Api]:
    fake = Api()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    vendor._exchanged.clear()  # pyright: ignore[reportPrivateUsage]
    yield fake
    vendor.transport = before


def level(name: str) -> str:
    return vendor.OPERATIONS[name][1].level


def target(fields: dict[str, Any]) -> Target:
    return Target("t", Env.DEV, None, Secret("r", json.dumps(fields)))


def typed(operation: str, body: dict[str, Any] | None = None) -> str | None:
    args = {"operation": operation, "body": body or {}}
    return destructive.reason(InstanceType.VENDOR, "call_operation", args)


# --- The rules ---


@pytest.mark.parametrize(
    ("name", "wanted"),
    [
        ("bluesky.list_posts", READ), ("bluesky.get_thread", READ),
        ("bluesky.create_post", FINAL), ("bluesky.delete_post", FINAL),
        ("facebook.list_posts", READ), ("facebook.list_comments", READ),
        ("facebook.page_insights", READ), ("facebook.hide_comment", HELD),
        ("facebook.publish_post", FINAL), ("facebook.edit_post", FINAL),
        ("facebook.reply_to_comment", FINAL), ("facebook.delete_post", FINAL),
        ("instagram.list_media", READ), ("instagram.account_insights", READ),
        ("instagram.prepare_post", HELD), ("instagram.publish_post", FINAL),
        ("instagram.reply_to_comment", FINAL), ("instagram.delete_comment", FINAL),
        ("threads.list_posts", READ), ("threads.list_replies", READ),
        ("threads.prepare_post", HELD), ("threads.publish_post", FINAL),
        ("threads.delete_post", FINAL),
    ],
)  # fmt: skip
def test_what_each_needs(name: str, wanted: str) -> None:
    assert level(name) == wanted


def test_a_page_post_only_waits_if_it_plainly_isnt_published() -> None:
    assert typed("facebook.create_post", {"message": "Hi"}) == social.PUBLISHES
    assert typed("facebook.create_post", {"message": "Hi", "published": True}) == social.PUBLISHES
    assert typed("facebook.create_post", {"published": "maybe"}) == social.PUBLISHES
    assert typed("facebook.create_post", {"published": False}) is None
    scheduled = {"published": "false", "scheduled_publish_time": 1900000000}
    assert typed("facebook.create_post", scheduled) is None


def test_none_of_them_can_be_trusted_for_an_hour() -> None:
    for key in ("bluesky", "facebook", "instagram", "threads"):
        assert vendor.VENDORS[key].never_trusted


@pytest.mark.parametrize("key", ["facebook", "instagram", "threads"])
def test_a_meta_read_can_never_ask_for_a_pages_token(key: str) -> None:
    """A read that took its fields from the call could ask a Page for its access_token."""
    for op in vendor.VENDORS[key].ops:
        reads_a_record = op.level == READ and op.answer is None and "insights" not in op.path
        if reads_a_record:
            assert "?fields=" in op.path, op.name
            assert "access_token" not in op.path, op.name
            assert "fields" not in op.params, op.name


async def test_a_call_cant_change_a_reads_fields(api: Api) -> None:
    op = vendor.OPERATIONS["facebook.get_page"][1]
    await vendor.run(vendor.VENDORS["facebook"], op, META, query={"fields": "access_token"},
                     account="111")  # fmt: skip
    assert "access_token" not in api.last.url.params["fields"]


def test_each_project_can_be_held_to_one_account() -> None:
    assert remote.VENDOR_SCOPES["facebook"].fields[0].args == ("account",)
    assert remote.VENDOR_SCOPES["instagram"].fields[0].args == ("account",)


# --- Bluesky ---


async def test_bluesky_trades_the_app_password_once_and_posts_as_the_handle(api: Api) -> None:
    v = vendor.VENDORS["bluesky"]
    op = vendor.OPERATIONS["bluesky.create_post"][1]
    await vendor.run(v, op, BLUESKY, body={"text": "Hello"})
    await vendor.run(v, op, BLUESKY, body={"text": "Again"})
    logins = [r for r in api.requests if r.url.path.endswith("createSession")]
    assert len(logins) == 1
    assert json.loads(logins[0].content) == {
        "identifier": "me.bsky.social", "password": "abcd-efgh-ijkl-mnop",
    }  # fmt: skip
    assert api.last.headers["authorization"] == "Bearer bsky-session"
    sent = json.loads(api.last.content)
    assert sent["repo"] == "me.bsky.social"
    assert sent["collection"] == "app.bsky.feed.post"
    assert sent["record"]["text"] == "Again"
    assert sent["record"]["createdAt"]


async def test_a_call_cant_write_anything_but_a_post(api: Api) -> None:
    connection = target(BLUESKY)
    args = {"operation": "bluesky.create_post", "body": {"collection": "app.bsky.graph.follow"}}
    assert vendor.call_operation.classify is not None
    with pytest.raises(ToolError, match="doesn't take collection"):
        vendor.call_operation.classify(connection, args)


async def test_a_link_counts_in_bytes(api: Api) -> None:
    text = "Café news: https://example.com/a."
    op = vendor.OPERATIONS["bluesky.create_post"][1]
    await vendor.run(vendor.VENDORS["bluesky"], op, BLUESKY, body={"text": text})
    facet = json.loads(api.last.content)["record"]["facets"][0]
    start, end = facet["index"]["byteStart"], facet["index"]["byteEnd"]
    assert text.encode()[start:end] == b"https://example.com/a"
    assert facet["features"][0]["uri"] == "https://example.com/a"


def test_a_post_thats_empty_or_too_long_is_refused_before_its_held() -> None:
    connection = target(BLUESKY)
    assert vendor.call_operation.classify is not None
    for text, said in (("", "needs its text"), ("x" * 301, "stop at 300")):
        args = {"operation": "bluesky.create_post", "body": {"text": text}}
        with pytest.raises(ToolError, match=said):
            vendor.call_operation.classify(connection, args)


def test_bluesky_wants_an_app_password_not_the_account_password() -> None:
    with pytest.raises(ToolError, match="doesn't look like"):
        vendor.settings(json.dumps({**BLUESKY, "app_password": "my-real-password"}))
    assert vendor.settings(json.dumps(BLUESKY))["repo"] == "me.bsky.social"


# --- Facebook and Instagram ---


async def test_each_page_uses_its_own_token(api: Api) -> None:
    v = vendor.VENDORS["facebook"]
    op = vendor.OPERATIONS["facebook.list_posts"][1]
    await vendor.run(v, op, META, account="222")
    assert api.last.url.path == "/v27.0/222/feed"
    assert api.last.url.params["access_token"] == "page-token-2"
    assert "authorization" not in api.last.headers


async def test_a_change_sends_its_token_in_the_form(api: Api) -> None:
    v = vendor.VENDORS["facebook"]
    op = vendor.OPERATIONS["facebook.create_post"][1]
    await vendor.run(v, op, META, body={"message": "Hi", "published": False}, account="111")
    assert "access_token" not in str(api.last.url)
    assert form(api.last) == {"message": "Hi", "published": "false",
                              "access_token": "page-token-1"}  # fmt: skip


async def test_a_page_this_sign_in_doesnt_reach_is_refused(api: Api) -> None:
    op = vendor.OPERATIONS["facebook.list_posts"][1]
    with pytest.raises(ToolError, match="isn't a Page"):
        await vendor.run(vendor.VENDORS["facebook"], op, META, account="999")
    assert not api.requests


async def test_listing_pages_shows_no_token_and_only_the_projects_page(api: Api) -> None:
    op = vendor.OPERATIONS["facebook.list_pages"][1]
    everything = await vendor.run(vendor.VENDORS["facebook"], op, META)
    assert [p["id"] for p in everything["pages"]] == ["111", "222"]
    assert "token" not in json.dumps(everything)
    held_to_one = await vendor.run(vendor.VENDORS["facebook"], op, META, account="111")
    assert [p["id"] for p in held_to_one["pages"]] == ["111"]
    assert not api.requests


def test_every_pages_token_is_scrubbed() -> None:
    found = vendor.secret_fragments(json.dumps(META))
    assert {"user-token", "page-token-1", "page-token-2"} <= set(found)


async def test_instagram_uses_the_linked_pages_token(api: Api) -> None:
    v = vendor.VENDORS["instagram"]
    fields = {**META, "vendor": "instagram"}
    await vendor.run(v, vendor.OPERATIONS["instagram.list_media"][1], fields, account="901")
    assert api.last.url.path == "/v27.0/901/media"
    assert api.last.url.params["access_token"] == "page-token-2"
    with pytest.raises(ToolError, match="isn't an Instagram account"):
        await vendor.run(v, vendor.OPERATIONS["instagram.list_media"][1], fields, account="111")


async def test_a_prepared_post_is_two_steps_with_different_holds(api: Api) -> None:
    v = vendor.VENDORS["instagram"]
    fields = {**META, "vendor": "instagram"}
    body = {"image_url": "https://example.com/a.jpg", "caption": "Hi"}
    await vendor.run(v, vendor.OPERATIONS["instagram.prepare_post"][1], fields, body=body,
                     account="901")  # fmt: skip
    assert api.last.url.path == "/v27.0/901/media"
    await vendor.run(v, vendor.OPERATIONS["instagram.publish_post"][1], fields,
                     body={"creation_id": "c1"}, account="901")  # fmt: skip
    assert api.last.url.path == "/v27.0/901/media_publish"
    assert form(api.last)["creation_id"] == "c1"


# --- Threads ---


async def test_threads_uses_its_own_address_and_the_signed_in_account(api: Api) -> None:
    fields = {"vendor": "threads", "access_token": "th-token", "default_account": "777"}
    v = vendor.VENDORS["threads"]
    await vendor.run(v, vendor.OPERATIONS["threads.prepare_post"][1], fields,
                     body={"media_type": "TEXT", "text": "Hi"})  # fmt: skip
    assert api.last.url.host == "graph.threads.com"
    assert api.last.url.path == "/v1.0/777/threads"
    assert form(api.last)["access_token"] == "th-token"


# --- Meta's sign-in ---


class Meta:
    """Facebook's token address answers GET; Threads' answers POST."""

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.pages: list[dict[str, Any]] = [
            {"id": "111", "name": "General Informatics", "access_token": "pt-1"},
            {"id": "222", "name": "Second", "access_token": "pt-2",
             "instagram_business_account": {"id": "901", "username": "second"}},
        ]  # fmt: skip
        self.renewals = 0
        self.refuse_renewal = False

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        query = request.url.params
        path = request.url.path
        if path.endswith("/refresh_access_token"):
            self.renewals += 1
            if self.refuse_renewal:
                return httpx2.Response(400, json={"error": {"message": "no"}})
            return httpx2.Response(200, json={"access_token": "th-3", "expires_in": 5184000})
        if path.endswith("/access_token") and query.get("grant_type") == "th_exchange_token":
            return httpx2.Response(200, json={"access_token": "th-2", "expires_in": 5184000})
        if path.endswith("/oauth/access_token"):
            if query.get("grant_type") == "fb_exchange_token":
                return httpx2.Response(200, json={"access_token": "long", "expires_in": 5184000})
            return httpx2.Response(200, json={"access_token": "short", "expires_in": 3600})
        if path.endswith("/me/accounts"):
            return httpx2.Response(200, json={"data": self.pages})
        return httpx2.Response(200, json={"id": "777", "name": "Pat Doe", "username": "pat"})


@pytest.fixture
def meta(monkeypatch: pytest.MonkeyPatch, database_url: str) -> Meta:
    fake = Meta()
    monkeypatch.setattr(developer_apps, "transport", httpx2.MockTransport(fake.handle))
    return fake


def settings() -> Settings:
    return get_settings().model_copy(update={"web_origin": "http://localhost:17845"})


def sign_in(key: str, connection: dict[str, Any] | None = None) -> str:
    provider = vendor.VENDORS[key].provider
    if not developer_apps.list_apps(settings(), provider):
        developer_apps.save_app(settings(), provider, "Mine", "app-1", "secret-1")
    wanted = {"shared": True, "env": "dev", **(connection or {})}
    url, state = developer_apps.start(settings(), key, wanted)
    found = developer_apps.take(state)
    with session_scope() as session:
        return developer_apps.finish(session, settings(), found, state, "code-1", {}) or url


def stored(name: str) -> dict[str, Any]:
    with session_scope(commit=False) as session:
        ref = instances.get_instance(session, name).secret_ref
    assert ref is not None
    return json.loads(get_vault().resolve(ref).reveal())


def test_facebook_keeps_a_lasting_token_for_each_page(meta: Meta) -> None:
    name = sign_in("facebook")
    secret = stored(name)
    assert secret["access_token"] == "long"
    assert [(p["id"], p["token"]) for p in secret["pages"]] == [("111", "pt-1"), ("222", "pt-2")]
    assert secret["refresh_token"] == ""
    first = meta.requests[0]
    assert first.method == "GET"
    assert first.url.params["code"] == "code-1"
    accounts = next(r for r in meta.requests if r.url.path.endswith("/me/accounts"))
    assert "instagram" not in accounts.url.params["fields"]


def test_one_page_is_the_default(meta: Meta) -> None:
    meta.pages = meta.pages[:1]
    assert stored(sign_in("facebook"))["default_account"] == "111"


def test_instagram_needs_a_linked_account(meta: Meta) -> None:
    meta.pages = meta.pages[:1]
    with pytest.raises(SignInError, match="Instagram professional account"):
        sign_in("instagram")


def test_instagram_finds_the_linked_account(meta: Meta) -> None:
    secret = stored(sign_in("instagram"))
    assert secret["default_account"] == "901"
    assert secret["pages"][1]["instagram"]["username"] == "second"


def test_no_pages_is_said_plainly(meta: Meta) -> None:
    meta.pages = []
    with pytest.raises(SignInError, match="No Pages were allowed"):
        sign_in("facebook")


def test_the_consent_page_asks_for_the_vendors_own_permissions(meta: Meta) -> None:
    developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1")
    url, _ = developer_apps.start(settings(), "instagram", {"shared": True})
    scope = parse_qs(urlsplit(url).query)["scope"][0].split(",")
    assert "instagram_content_publish" in scope
    assert "pages_manage_posts" not in scope


@pytest.mark.parametrize("key", ["facebook", "instagram", "threads"])
def test_a_clients_account_needs_meta_review(key: str, meta: Meta) -> None:
    provider = vendor.VENDORS[key].provider
    developer_apps.save_app(settings(), provider, "Mine", "app-1", "secret-1")
    with pytest.raises(RuleViolationError, match="needs Meta review"):
        developer_apps.start(settings(), key, {"owner": "acme"})
    developer_apps.start(settings(), key, {"shared": True})


def test_the_code_path_for_clients_is_ready_once_meta_approves(meta: Meta) -> None:
    developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1",
                            {"client_accounts": "approved"})  # fmt: skip
    url, _ = developer_apps.start(settings(), "facebook", {"owner": "acme"})
    assert url.startswith("https://www.facebook.com/v27.0/dialog/oauth")


def test_saying_meta_approved_keeps_the_apps_secret(meta: Meta) -> None:
    app = developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1")
    changed = developer_apps.set_options(
        settings(), "meta", app.id, {"client_accounts": "approved"}
    )
    assert changed.client_secret == "secret-1"
    assert developer_apps.list_apps(settings(), "meta")[0].options == changed.options
    with pytest.raises(RuleViolationError, match="Choose the client accounts"):
        developer_apps.set_options(settings(), "meta", app.id, {"client_accounts": "maybe"})


def test_an_app_defaults_to_needing_review(meta: Meta) -> None:
    app = developer_apps.save_app(settings(), "meta", "Mine", "app-1", "secret-1")
    assert app.options["client_accounts"] == "needs_review"


def test_meta_comes_back_by_the_https_page() -> None:
    for key in ("meta", "threads"):
        assert developer_apps.PROVIDERS[key].hosted


def test_threads_gets_a_long_token(meta: Meta) -> None:
    secret = stored(sign_in("threads"))
    assert secret["access_token"] == "th-2"
    assert secret["default_account"] == "777"
    assert secret["expires_at"] > time.time() + 59 * 24 * 3600
    assert meta.requests[0].method == "POST"


def _age(name: str, days_left: float) -> str:
    with session_scope(commit=False) as session:
        ref = instances.get_instance(session, name).secret_ref
    assert ref is not None
    secret = {**stored(name), "expires_at": time.time() + days_left * 24 * 3600}
    get_vault().store(ref, json.dumps(secret))
    return ref


def test_a_threads_token_is_renewed_while_its_in_use(meta: Meta) -> None:
    name = sign_in("threads")
    ref = _age(name, 50)
    developer_apps.resolve(get_vault(), name, ref)
    assert meta.renewals == 0
    _age(name, 20)
    fresh = developer_apps.resolve(get_vault(), name, ref)
    assert meta.renewals == 1
    assert json.loads(fresh.reveal())["access_token"] == "th-3"
    assert stored(name)["access_token"] == "th-3"


def test_a_failed_renewal_doesnt_stop_a_token_thats_still_good(meta: Meta) -> None:
    name = sign_in("threads")
    ref = _age(name, 20)
    meta.refuse_renewal = True
    assert json.loads(developer_apps.resolve(get_vault(), name, ref).reveal())["access_token"]
    _age(name, -1)
    with pytest.raises(SignInError):
        developer_apps.resolve(get_vault(), name, ref)


def test_facebook_has_nothing_to_renew(meta: Meta) -> None:
    name = sign_in("facebook")
    ref = _age(name, -1)
    developer_apps.resolve(get_vault(), name, ref)
    assert meta.renewals == 0


def test_the_http_client_never_logs_an_address_with_a_token() -> None:
    import logging  # noqa: PLC0415

    assert logging.getLogger("httpx2").getEffectiveLevel() >= logging.WARNING


def test_a_read_classifies_as_a_read() -> None:
    args = {"operation": "facebook.list_posts", "account": "111"}
    assert vendor.call_operation.classify is not None
    assert vendor.call_operation.classify(target(META), args) is ToolKind.READ
