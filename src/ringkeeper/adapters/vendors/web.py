"""Websites: PageSpeed Insights, Google's free check of a page's speed, with a key from Google
Cloud.

It only reads: a check loads the page the way a visitor's browser would, and changes nothing.
Its whole answer is a Lighthouse report of several hundred kilobytes, so what comes back is the
part people act on: the scores, the lab metrics, real visitors' Core Web Vitals when Google has
them, and the few fixes that would save the most time. The key goes in a header, never in an
address, so it can't end up in a log.
"""

from typing import Any, cast
from urllib.parse import urlsplit

from ringkeeper.adapters.base import ToolError
from ringkeeper.adapters.vendor_spec import JSON, Auth, Secret, Vendor, reads

CATEGORY = "Other"
CATEGORIES = ("performance", "accessibility", "best-practices", "seo")
METRICS = (
    "first-contentful-paint", "largest-contentful-paint", "total-blocking-time",
    "cumulative-layout-shift", "speed-index",
)  # fmt: skip
FIXES = 5
# A page anyone may check, for the Test button: an SEO-only check is the quickest there is.
TEST_PAGE = "https://www.example.com/"


def _query(query: JSON, _local: JSON) -> JSON:
    """What Google is asked: the page, a phone or a computer, and which scores (a list, or
    words separated by commas)."""
    url = str(query.get("url") or "").strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ToolError("give the page's full address, like https://www.example.com/")
    strategy = str(query.get("strategy") or "mobile").lower()
    if strategy not in ("mobile", "desktop"):
        raise ToolError("strategy is mobile or desktop")
    asked: Any = query.get("category") or ["performance"]
    words = asked.split(",") if isinstance(asked, str) else [str(c) for c in cast(list[Any], asked)]
    wanted = [w.strip().lower().replace("_", "-") for w in words if w.strip()]
    unknown = sorted(set(wanted) - set(CATEGORIES))
    if unknown:
        raise ToolError(f"{', '.join(unknown)} isn't a score: choose from {', '.join(CATEGORIES)}")
    scores = [w.upper().replace("-", "_") for w in dict.fromkeys(wanted)]
    sent: JSON = {"url": url, "strategy": strategy.upper(), "category": scores}
    if query.get("locale"):
        sent["locale"] = str(query["locale"])
    return sent


def _savings(audit: JSON) -> float:
    details = cast(JSON, audit.get("details") or {})
    if details.get("overallSavingsMs") is not None:
        return float(details["overallSavingsMs"] or 0)
    found = cast(JSON, audit.get("metricSavings") or {})
    return float(found.get("LCP") or 0) + float(found.get("FCP") or 0)


def _fixes(audits: JSON) -> list[JSON]:
    """The fixes that would save the most time, as Lighthouse words them."""
    found: list[tuple[float, JSON]] = []
    for key, value in audits.items():
        audit = cast(JSON, value)
        score = audit.get("score")
        if score is None or float(score) >= 0.9:
            continue
        saves = _savings(audit)
        if saves <= 0:
            continue
        found.append((saves, {
            "id": key, "title": audit.get("title"), "saves": audit.get("displayValue") or "",
            "saves_ms": round(saves),
        }))  # fmt: skip
    return [item for _, item in sorted(found, key=lambda pair: -pair[0])[:FIXES]]


def _field(experience: Any) -> JSON | None:
    """Real visitors' numbers (Chrome's), when Google has enough of them for this page."""
    if not isinstance(experience, dict):
        return None
    data = cast(JSON, experience)
    metrics = cast(JSON, data.get("metrics") or {})
    if not metrics:
        return None
    return {
        "overall": data.get("overall_category"),
        "for_whole_site": bool(data.get("origin_fallback")),
        "metrics": {
            name.lower(): {
                "p75": cast(JSON, m).get("percentile"),
                "rating": cast(JSON, m).get("category"),
            }
            for name, m in metrics.items()
        },
    }


def _report(answer: JSON, _local: JSON) -> JSON:
    lighthouse = cast(JSON, answer.get("lighthouseResult") or {})
    categories = cast(JSON, lighthouse.get("categories") or {})
    audits = cast(JSON, lighthouse.get("audits") or {})
    scores: dict[str, int | None] = {}
    for key, category in categories.items():
        score = cast(JSON, category).get("score")
        scores[key] = round(float(score) * 100) if score is not None else None
    lab = {key: cast(JSON, audits[key]).get("displayValue") for key in METRICS if key in audits}
    return {
        "page": (
            lighthouse.get("finalDisplayedUrl") or lighthouse.get("finalUrl") or answer.get("id")
        ),
        "device": str(cast(JSON, lighthouse.get("configSettings") or {}).get("formFactor") or ""),
        "checked_at": answer.get("analysisUTCTimestamp") or lighthouse.get("fetchTime"),
        "scores": scores,
        "lab": lab,
        "real_visitors": _field(answer.get("loadingExperience")),
        "biggest_fixes": _fixes(audits),
        "warnings": lighthouse.get("runWarnings") or [],
    }


def _test(_query_in: JSON, _local: JSON) -> JSON:
    return {"url": TEST_PAGE, "strategy": "DESKTOP", "category": ["SEO"]}


def _who(found: JSON) -> str:
    return f"the key works (checked {found.get('page') or TEST_PAGE})"


PAGESPEED = Vendor(
    "pagespeed", "PageSpeed Insights", CATEGORY, "A page's speed and Core Web Vitals",
    "https://pagespeedonline.googleapis.com/pagespeedonline/v5",
    auth=Auth("header", name="X-Goog-Api-Key"),
    secrets=(Secret("api_key", "API key", "A Google Cloud API key: AIza...",
                    pattern=r"^AIza[0-9A-Za-z_\-]{35}$"),),
    probe="test_key", who=_who,
    env_vars={"PAGESPEED_API_KEY": "api_key", "PSI_API_KEY": "api_key"},
    words=("pagespeed", "page speed", "lighthouse", "core web vitals", "speed", "performance",
           "seo", "website", "psi"),
    key_url="https://developers.google.com/speed/docs/insights/v5/get-started",
    steps="In Google Cloud, pick or make a project, enable the PageSpeed Insights API, then "
    "under APIs & Services > Credentials create an API key. Restrict it to the PageSpeed "
    "Insights API. It's free.",
    note="It only reads: a check loads the page like a visitor would. One key works for every "
    "client's sites, so add it as your own tool.",
    timeout=120.0,
    ops=(
        reads("check_page", "/runPagespeed",
              "Check a page's speed: scores, lab metrics, real visitors' Core Web Vitals and the "
              "biggest fixes", "url", "strategy", "category", "locale",
              prepare=_query, after=_report,
              hint="url: the page's full address. strategy: mobile (the default) or desktop. "
              "category: performance (the default), accessibility, best-practices, seo, or "
              "several separated by commas. A check takes 10 to 60 seconds."),
        reads("test_key", "/runPagespeed", "Whether the key works (a quick check of example.com)",
              prepare=_test, after=_report),
    ),
)  # fmt: skip

VENDORS = (PAGESPEED,)
