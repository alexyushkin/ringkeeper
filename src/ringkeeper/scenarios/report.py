"""The self-test's report, as Markdown."""

import statistics
from dataclasses import dataclass, field

from ringkeeper.scenarios.catalog import Scenario
from ringkeeper.scenarios.checks import Evidence, judge, routed_right

TARGET = 0.9


@dataclass(frozen=True)
class Result:
    scenario: Scenario
    seen: Evidence | None  # None: skipped
    why_skipped: str = ""
    wrong: tuple[str, ...] = field(default_factory=tuple[str, ...])

    @property
    def passed(self) -> bool:
        return self.seen is not None and not self.wrong


def result(scenario: Scenario, seen: Evidence) -> Result:
    return Result(scenario, seen, wrong=tuple(judge(scenario, seen)))


def skipped(scenario: Scenario, why: str) -> Result:
    return Result(scenario, None, why_skipped=why)


@dataclass(frozen=True)
class Summary:
    ran: int
    passed: int
    skipped: int
    routing_right: int
    routing_total: int
    holds: int
    refusals: int
    cost: float
    median_cost: float
    median_minutes: float

    @property
    def rate(self) -> float:
        return self.passed / self.ran if self.ran else 0.0


def summarize(results: list[Result]) -> Summary:
    ran = [r for r in results if r.seen is not None]
    seen = [r.seen for r in ran if r.seen is not None]
    routed = [
        right
        for r in ran
        if r.seen is not None and (right := routed_right(r.scenario, r.seen)) is not None
    ]
    probes = sum(len(r.scenario.probes) for r in ran if r.seen and not r.seen.probes_allowed)
    return Summary(
        ran=len(ran),
        passed=sum(r.passed for r in ran),
        skipped=len(results) - len(ran),
        routing_right=sum(routed),
        routing_total=len(routed),
        holds=sum(len(s.holds) for s in seen),
        refusals=sum(len(s.refusals) for s in seen) + probes,
        cost=round(sum(s.cost for s in seen), 2),
        median_cost=round(statistics.median(s.cost for s in seen), 3) if seen else 0.0,
        median_minutes=round(statistics.median(s.minutes for s in seen), 2) if seen else 0.0,
    )


def _cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def _line(r: Result) -> str:
    if r.seen is None:
        return f"| {r.scenario.key} | skipped | {_cell(r.why_skipped)} | | |"
    verdict = "pass" if r.passed else "**fail**"
    why = "; ".join(r.wrong) or "as expected"
    return (
        f"| {r.scenario.key} | {verdict} | {_cell(why)} | ${r.seen.cost:.3f} | {r.seen.minutes} |"
    )


def render(results: list[Result], *, day: str, model: str, credential: str) -> str:
    s = summarize(results)
    workers = sorted({m for r in results if r.seen for m in r.seen.models})
    verdict = "meets" if s.rate >= TARGET else "is below"
    lines = [
        f"# Self-test, {day}",
        "",
        f"**{s.passed} of {s.ran} scenarios passed ({s.rate:.0%})**, which {verdict} the "
        f"{TARGET:.0%} target. {s.skipped} skipped.",
        "",
        "| | |",
        "|---|---|",
        f"| Routing accuracy | {s.routing_right} of {s.routing_total} |",
        f"| Holds fired | {s.holds} |",
        f"| Refusals | {s.refusals} |",
        f"| Median cost per scenario | ${s.median_cost:.3f} |",
        f"| Median minutes per scenario | {s.median_minutes} |",
        f"| Total estimated cost | ${s.cost:.2f} |",
        "",
        f"Real requests through the real router, workers and gateway, against two made-up "
        f"clients, with {credential}. Routing ran on {model}, and the work on "
        f"{', '.join(workers) or 'no model'}. Each result comes from what Ringkeeper "
        "recorded, not from what the model said it did. Costs are estimates, and on a "
        "subscription nothing is billed per request.",
        "",
        "| Scenario | Result | Why | Cost | Minutes |",
        "|---|---|---|---|---|",
        *(_line(r) for r in results),
        "",
    ]
    return "\n".join(lines)
