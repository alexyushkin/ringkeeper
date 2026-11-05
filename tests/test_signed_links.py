"""Signed links never stay in logs, audit rows or conversations."""

import json
import logging
from typing import Any

import httpx2
import pytest
from sqlalchemy import select

from ringkeeper import signed_links
from ringkeeper.adapters import vendor
from ringkeeper.calls import Caller, ToolCall
from ringkeeper.db import session_scope
from ringkeeper.models import AuditEvent, InstanceType
from ringkeeper.scrub import install_log_scrubbing, scrub, scrub_obj
from ringkeeper.services.calls import RefusalError, run_call
from ringkeeper.vault import Vault, get_vault
from tests.helpers import bind, make_instance, make_project

pytestmark = pytest.mark.anyio
MASK = signed_links.MASK
SIGNED = [
    # Lemon Squeezy's customer portal
    "https://acme.lemonsqueezy.com/billing?expires=1790000000&signature=abcdef0123456789",
    "https://acme.lemonsqueezy.com/subscription/1/payment-details?expires=179&signature=abc",
    # S3, Google Cloud Storage, Azure, CloudFront
    "https://b.s3.amazonaws.com/k.pdf?X-Amz-Algorithm=AWS4&X-Amz-Signature=deadbeef",
    "https://storage.googleapis.com/b/k.pdf?X-Goog-Algorithm=GOOG4&X-Goog-Signature=abc123",
    "https://a.blob.core.windows.net/c/k.pdf?sv=2024&se=2026-12-01&sig=abc%2Bdef%3D",
    "https://d.cloudfront.net/k.pdf?Expires=1790&Signature=abc~def&Key-Pair-Id=K1",
    # OneDrive's download address
    "https://acme.sharepoint.com/_layouts/15/download.aspx?UniqueId=1&tempauth=v1.abc",
    # A token with an end date works the same way
    "https://cdn.example.com/video.mp4?token=abc123&expires=1790000000",
]
ORDINARY = [
    "https://example.com/page?id=7&sort=name",
    "https://example.com/search?q=signature",
    "https://example.com/design?signature_style=bold",
    "https://example.com/a?expires=2026-12-01",
    "visit example.com?signature=abc for more",
]


@pytest.mark.parametrize("link", SIGNED)
def test_a_signed_link_keeps_where_it_points_and_loses_the_rest(link: str) -> None:
    masked = signed_links.mask(f"Open {link} now")
    assert masked == f"Open {link.split('?', maxsplit=1)[0]}?{MASK} now"


@pytest.mark.parametrize("text", ORDINARY)
def test_an_ordinary_address_is_left_alone(text: str) -> None:
    assert signed_links.mask(text) == text


def test_the_end_of_a_sentence_stays() -> None:
    assert signed_links.mask("(see https://a.example/b?signature=x).") == (
        f"(see https://a.example/b?{MASK})."
    )


def test_a_link_in_escaped_json_or_inside_another_address_is_found() -> None:
    held = json.dumps({"url": SIGNED[0].replace("/", "\\/")})
    assert "abcdef" not in signed_links.mask(held)
    assert json.loads(signed_links.mask(held))  # the JSON around it still reads
    escaped = "https://a.example/b?x=1\\u0026signature=abc"
    assert "abc" not in signed_links.mask(escaped)
    nested = "https://app.example/go?to=https%3A%2F%2Fb.example%2Fc%3Fsignature%3Dabc"
    assert "signature" not in signed_links.mask(nested)
    html = "https://a.example/b?x=1&amp;signature=abc"
    assert "abc" not in signed_links.mask(html)


def test_found_looks_everywhere() -> None:
    assert signed_links.found({"a": [{"b": SIGNED[0]}]})
    assert not signed_links.found({"a": ["https://example.com/x"]})


def test_scrubbing_takes_them_out_of_everything_scrubbed() -> None:
    assert "abcdef" not in scrub(f"portal: {SIGNED[0]}")
    kept = scrub_obj({"urls": {"customer_portal": SIGNED[0]}})
    assert kept["urls"]["customer_portal"].endswith(MASK)


def test_a_log_line_never_holds_one(caplog: pytest.LogCaptureFixture) -> None:
    install_log_scrubbing()
    with caplog.at_level(logging.INFO):
        logging.getLogger("ringkeeper.test").info("fetched %s", SIGNED[2])
    assert "deadbeef" not in caplog.text
    assert MASK in caplog.text


@pytest.fixture
def linked(database_url: str) -> Vault:
    vault = get_vault()
    ref = "file://ringkeeper/connections/acme-lemon"
    vault.store(ref, json.dumps({"vendor": "lemonsqueezy", "api_key": "lemon-test-key-value"}))
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_instance(
            session, "acme-lemon", type=InstanceType.VENDOR, secret_ref=ref,
            config={"vendor": "lemonsqueezy"},
        )  # fmt: skip
    bind("acme-web", "acme-lemon")
    return vault


def call(**args: Any) -> ToolCall:
    caller = Caller("acme-web", "session-1", "token-1")
    args = {"operation": "lemonsqueezy.list_customers"} | args
    return ToolCall(caller, InstanceType.VENDOR, "call_operation", "acme-lemon", args)


async def test_a_result_with_a_portal_link_reaches_the_agent_without_it(
    linked: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    def answer(_request: httpx2.Request) -> httpx2.Response:
        urls = {"customer_portal": SIGNED[0], "update_payment_method": SIGNED[1]}
        return httpx2.Response(200, json={"data": [{"id": "1", "attributes": {"urls": urls}}]})

    monkeypatch.setattr(vendor, "transport", httpx2.MockTransport(answer))
    result = await run_call(call(), linked)
    assert "abcdef" not in json.dumps(result)
    assert MASK in json.dumps(result)


async def test_a_call_carrying_one_is_refused_and_says_why(linked: Vault) -> None:
    with pytest.raises(RefusalError, match="signed link"):
        await run_call(call(query={"redirect": SIGNED[0]}), linked)
    with session_scope() as session:
        rows = list(session.scalars(select(AuditEvent)))
        assert rows
        assert "abcdef" not in json.dumps([str(r.args) for r in rows])
