"""PageSpeed Insights, against a fake of Google's API: it only reads, the key goes in a header and
never in an address, the answer is the part people act on, and recipes can ask for it by name."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest

from ringkeeper.adapters import vendor
from ringkeeper.adapters.base import Target, ToolError
from ringkeeper.adapters.vendor_spec import READ
from ringkeeper.adapters.vendors.web import PAGESPEED, TEST_PAGE
from ringkeeper.db import session_scope
from ringkeeper.models import Env, InstanceType
from ringkeeper.schemas import BindingIn, InstanceIn
from ringkeeper.services import bindings, env_scan, instances, recipes
from ringkeeper.vault import Secret
from tests.helpers import make_project

pytestmark = pytest.mark.anyio
KEY = "AIza" + "S" * 35
PAGE = "https://www.geninfos.com/"


def lighthouse(url: str) -> dict[str, Any]:
    """The parts of a real answer that matter, and some that don't."""
    return {
        "id": url,
        "analysisUTCTimestamp": "2026-11-08T13:00:00.000Z",
        "loadingExperience": {
            "overall_category": "AVERAGE", "origin_fallback": False,
            "metrics": {
                "LARGEST_CONTENTFUL_PAINT_MS": {"percentile": 2900, "category": "AVERAGE"},
                "INTERACTION_TO_NEXT_PAINT": {"percentile": 120, "category": "FAST"},
            },
        },
        "lighthouseResult": {
            "finalDisplayedUrl": url, "configSettings": {"formFactor": "mobile"},
            "runWarnings": [],
            "categories": {"performance": {"score": 0.62}, "seo": {"score": 0.9}},
            "audits": {
                "largest-contentful-paint": {"score": 0.4, "displayValue": "4.1 s"},
                "cumulative-layout-shift": {"score": 1, "displayValue": "0.02"},
                "render-blocking-resources": {
                    "score": 0.3, "title": "Eliminate render-blocking resources",
                    "displayValue": "Potential savings of 1,200 ms",
                    "details": {"type": "opportunity", "overallSavingsMs": 1200},
                },
                "unused-javascript": {
                    "score": 0.5, "title": "Reduce unused JavaScript",
                    "displayValue": "Potential savings of 180 KiB",
                    "details": {"type": "opportunity", "overallSavingsMs": 450},
                },
                "image-delivery-insight": {
                    "score": 0, "title": "Improve image delivery", "metricSavings": {"LCP": 800},
                },
                "font-display": {"score": 1, "details": {"overallSavingsMs": 30}},
                "screenshot-thumbnails": {"score": None, "details": {"items": ["x" * 5000]}},
            },
        },
    }  # fmt: skip


class FakeGoogle:
    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []

    def handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.headers.get("X-Goog-Api-Key") != KEY:
            return httpx2.Response(400, json={"error": {"message": "API key not valid."}})
        return httpx2.Response(200, json=lighthouse(request.url.params["url"]))


@pytest.fixture
def google() -> Iterator[FakeGoogle]:
    fake = FakeGoogle()
    before = vendor.transport
    vendor.transport = httpx2.MockTransport(fake.handle)
    yield fake
    vendor.transport = before


def target() -> Target:
    fields = {"vendor": "pagespeed", "api_key": KEY}
    return Target("my-pagespeed", Env.PROD, None, Secret("r", json.dumps(fields)))


async def check(**query: Any) -> Any:
    return await vendor.call_operation.handler(
        target(), operation="pagespeed.check_page", query=query
    )


def test_it_only_reads() -> None:
    assert all(op.level == READ for op in PAGESPEED.ops)
    assert PAGESPEED.timeout >= 60  # a check takes up to a minute


async def test_a_check_asks_for_the_page_with_the_key_in_a_header(google: FakeGoogle) -> None:
    await check(url=PAGE, category="performance, SEO")
    [sent] = google.requests
    assert sent.url.path == "/pagespeedonline/v5/runPagespeed"
    assert KEY not in str(sent.url)  # never in an address, so never in a log
    asked = parse_qs(sent.url.query.decode())
    assert asked == {"url": [PAGE], "strategy": ["MOBILE"], "category": ["PERFORMANCE", "SEO"]}


async def test_the_answer_is_what_people_act_on(google: FakeGoogle) -> None:
    found = await check(url=PAGE, strategy="desktop")
    assert found["page"] == PAGE
    assert found["scores"] == {"performance": 62, "seo": 90}
    assert found["lab"] == {"largest-contentful-paint": "4.1 s", "cumulative-layout-shift": "0.02"}
    assert found["real_visitors"]["metrics"]["largest_contentful_paint_ms"] == {
        "p75": 2900, "rating": "AVERAGE",
    }  # fmt: skip
    assert [f["id"] for f in found["biggest_fixes"]] == [
        "render-blocking-resources", "image-delivery-insight", "unused-javascript",
    ]  # fmt: skip
    assert len(json.dumps(found)) < 3000  # not the whole Lighthouse report
    assert parse_qs(google.requests[0].url.query.decode())["strategy"] == ["DESKTOP"]


@pytest.mark.parametrize(
    ("query", "said"),
    [
        ({"url": "geninfos.com"}, "full address"),
        ({"url": "ftp://geninfos.com/"}, "full address"),
        ({"url": PAGE, "strategy": "tablet"}, "mobile or desktop"),
        ({"url": PAGE, "category": "speed"}, "isn't a score"),
    ],
)
async def test_a_check_it_cant_run_is_refused_before_calling(
    google: FakeGoogle, query: dict[str, Any], said: str
) -> None:
    with pytest.raises(ToolError, match=said):
        await check(**query)
    assert google.requests == []


async def test_the_test_button_says_the_key_works(google: FakeGoogle) -> None:
    said = await vendor.probe(target())
    assert said == f"Connected to PageSpeed Insights: the key works (checked {TEST_PAGE})."
    asked = parse_qs(google.requests[0].url.query.decode())
    assert asked["category"] == ["SEO"]


async def test_a_wrong_key_says_so(google: FakeGoogle) -> None:
    fields = {"vendor": "pagespeed", "api_key": "AIza" + "x" * 35}
    wrong = Target("p", Env.PROD, None, Secret("r", json.dumps(fields)))
    with pytest.raises(ToolError, match="API key not valid"):
        await vendor.probe(wrong)


def test_a_key_in_a_projects_env_is_found(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"PAGESPEED_API_KEY={KEY}\n")
    found = [row[0] for row in env_scan.scan(tmp_path) if row[0].label == "PageSpeed Insights"]
    assert len(found) == 1
    assert found[0].usable


# --- Recipes ask for it by name ---


def add(name: str, which: str) -> None:
    with session_scope() as session:
        instances.create_instance(session, InstanceIn(
            name=name, title=name, type=InstanceType.VENDOR, env=Env.PROD,
            secret_ref=f"file://you/{name}", owner=instances.SHARED_OWNER, shared=True,
            config={"vendor": which},
        ))  # fmt: skip
        bindings.set_binding(session, BindingIn(project="gi-web", instance=name))


def test_a_speed_recipe_offers_only_pagespeed_connections(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-web", owner="general-informatics")
    add("my-pagespeed", "pagespeed")
    add("my-clerk", "clerk")
    weekly = recipes.starters()["weekly-speed-check"]
    with session_scope() as session:
        found = recipes.needs(session, "gi-web", weekly)
    assert found["connections"] == {"vendor:pagespeed": ["my-pagespeed"]}
    with session_scope() as session:
        agent, made = recipes.add(
            session, "gi-web", weekly, connections={"vendor:pagespeed": "my-pagespeed"}, events=[]
        )
        assert list(agent.connections) == ["my-pagespeed"]
        assert [r.paused for r in made] == [True]  # like every added routine
    with session_scope() as session, pytest.raises(Exception, match="isn't a vendor:pagespeed"):
        recipes.add(session, "gi-web", weekly, connections={"vendor:pagespeed": "my-clerk"},
                    events=[])  # fmt: skip


def test_an_export_names_the_business_system(database_url: str) -> None:
    with session_scope() as session:
        make_project(session, "gi-web", owner="general-informatics")
    add("my-pagespeed", "pagespeed")
    weekly = recipes.starters()["weekly-speed-check"]
    with session_scope() as session:
        agent, _ = recipes.add(
            session, "gi-web", weekly, connections={"vendor:pagespeed": "my-pagespeed"}, events=[]
        )
        exported = recipes.export(session, "gi-web", agent.name)
    assert exported.agent.needs == ["vendor:pagespeed"]


def test_a_recipe_cant_ask_for_a_business_system_ringkeeper_doesnt_know() -> None:
    agent = {"name": "x", "needs": ["vendor:nope"]}
    data: dict[str, Any] = {"ringkeeper_recipe": 1, "name": "x", "agent": agent, "routines": []}
    with pytest.raises(Exception, match="isn't a kind of connection"):
        recipes.parse_data(data)
