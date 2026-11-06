"""A call written the way it's natural to write it: the values reach the service in the part it
reads them from, and what can't be understood is refused in plain words."""

import json
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import httpx2
import pytest
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from ringkeeper.adapters import agent_name, vendor
from ringkeeper.adapters.base import Target
from ringkeeper.adapters.vendor import ARGUMENTS, OPERATIONS, PARTS, tidied
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.tokens import issue_token
from ringkeeper.vault import Secret, Vault, get_vault
from tests.helpers import bind, make_instance, make_project

pytestmark = pytest.mark.anyio
NAME = "acme-hubspot"
SEARCH = "hubspot.search_contacts"
MEANT = {"query": "Mike", "limit": 100}


class FakeApi:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        return httpx2.Response(200, json={"results": []})


@pytest.fixture
def api() -> Iterator[FakeApi]:
    fake = FakeApi()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


@pytest.fixture
def linked(database_url: str) -> Vault:
    vault = get_vault()
    ref = f"file://ringkeeper/connections/{NAME}"
    vault.store(ref, json.dumps({"vendor": "hubspot", "access_token": "hs-token"}))
    with session_scope() as session:
        make_project(session, "acme-web", owner="acme")
        make_instance(
            session, NAME, type=InstanceType.VENDOR, secret_ref=ref, config={"vendor": "hubspot"},
        )  # fmt: skip
    bind("acme-web", NAME)
    return vault


def client(url: str) -> Client[Any]:
    bearer, _ = issue_token("acme-web", timedelta(minutes=10))
    return Client(
        StreamableHttpTransport(
            f"{url}/mcp/{agent_name(InstanceType.VENDOR)}",
            headers={"Authorization": f"Bearer {bearer}"},
        )
    )


def text_of(result: Any) -> str:
    return "\n".join(getattr(block, "text", "") for block in result.content)


async def call(url: str, **args: Any) -> Any:
    async with client(url) as mcp:
        return await mcp.call_tool(
            "call_operation", {**args, "instance": NAME}, raise_on_error=False
        )


@pytest.mark.usefixtures("linked")
@pytest.mark.parametrize(
    "written",
    [
        {"query": "Mike", "limit": "100"},  # as it was seen: beside the parts
        {"query": {"query": "Mike", "limit": 100}},  # in a part the service doesn't read
        {"query": '{"query": "Mike", "limit": 100}'},  # as text
        {"body": MEANT},  # as it should be
    ],
    ids=["flat", "wrong-part", "text", "right"],
)
async def test_a_search_reaches_the_service_however_its_written(
    bare_gateway: str, api: FakeApi, written: dict[str, Any]
) -> None:
    result = await call(bare_gateway, operation=SEARCH, **written)
    assert not result.is_error, text_of(result)
    [sent] = api.requests
    assert json.loads(sent.content) == MEANT
    assert not sent.url.query


@pytest.mark.usefixtures("linked")
async def test_what_cant_be_understood_is_refused_in_plain_words(
    bare_gateway: str, api: FakeApi
) -> None:
    result = await call(bare_gateway, operation=SEARCH, nonsense="a-value-not-to-repeat", path=5)
    said = text_of(result)
    assert result.is_error
    assert said.startswith("call_operation wasn't run:")
    assert "`nonsense` isn't something this tool takes" in said
    assert "It takes: operation, path, query, body, account, instance." in said
    assert "pydantic" not in said
    assert "a-value-not-to-repeat" not in said
    assert not api.requests


@pytest.mark.usefixtures("linked")
async def test_any_tool_is_refused_in_plain_words(bare_gateway: str) -> None:
    async with client(bare_gateway) as mcp:
        result = await mcp.call_tool("read_more", {"offset": "x"}, raise_on_error=False)
    said = text_of(result)
    assert result.is_error
    assert said.startswith("read_more wasn't run:")
    assert "`continuation` is needed" in said
    assert "pydantic" not in said


@pytest.mark.parametrize(
    ("written", "meant"),
    [
        # The address of a read.
        ({"operation": "clerk.list_users", "limit": 5}, {"query": {"limit": 5}}),
        # A POST whose other values are in its address.
        (
            {"operation": "hubspot.enroll_in_sequence", "userId": "1", "contactId": "2"},
            {"query": {"userId": "1"}, "body": {"contactId": "2"}},
        ),
        ({"operation": "ghost.create_post", "newsletter": "n"}, {"query": {"newsletter": "n"}}),
        # A path that's really a header.
        ({"operation": "google_ads.pause_campaign", "manager": "1"}, {"path": {"manager": "1"}}),
        # GraphQL's variables are the body.
        (
            {"operation": "shopify.list_orders", "query": {"first": "5", "query": "open"}},
            {"body": {"first": 5, "query": "open"}},
        ),
        # An operation's own query, which is an object.
        (
            {"operation": "square.search_customers", "query": {"filter": {}}, "limit": 5},
            {"body": {"query": {"filter": {}}, "limit": 5}},
        ),
        # What's in the right part stays, and wins.
        (
            {"operation": SEARCH, "body": {"limit": 1}, "limit": 2, "query": "Mike"},
            {"body": {"limit": 1, "query": "Mike"}},
        ),
    ],
)
def test_values_go_where_the_operation_reads_them(
    written: dict[str, Any], meant: dict[str, Any]
) -> None:
    assert tidied(written) == {"operation": written["operation"], **meant}


@pytest.mark.parametrize(
    "written",
    [
        {"operation": "nobody.nothing", "limit": 5},  # not an operation: refused later
        {"operation": SEARCH, "nonsense": 1},  # not listed: refused
        {"operation": SEARCH, "account": "a", "instance": "i"},  # the tool's own
        {"operation": "clerk.list_users", "query": {"unlisted": 1}},  # asked for as it is
    ],
)
def test_nothing_else_moves(written: dict[str, Any]) -> None:
    assert tidied(written) == written


@pytest.mark.parametrize("name", sorted(OPERATIONS))
def test_every_operation_can_be_written_flat(name: str) -> None:
    table, op = OPERATIONS[name]
    parts = vendor._parts(table, op)  # pyright: ignore[reportPrivateUsage]
    written = {n: "x" for names in parts.values() for n in names}
    meant = tidied({"operation": name, **written})
    assert set(meant) <= set(ARGUMENTS)
    for part, names in parts.items():
        assert set(names) <= set(meant[part]), part
    assert tidied(meant) == meant
    assert all(isinstance(meant.get(part, {}), dict) for part in PARTS)


async def test_list_operations_says_which_part_each_value_goes_in() -> None:
    fields = {"vendor": "hubspot", "access_token": "hs-token"}
    target = Target("t", Env.DEV, None, Secret("r", json.dumps(fields)))
    listed = await vendor.list_operations.handler(target)
    [search] = [op for op in listed["operations"] if op["name"] == SEARCH]
    assert "limit" in search["body"]
    assert "query" in search["body"]
    assert "takes" not in search
