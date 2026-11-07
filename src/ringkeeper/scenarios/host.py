"""The self-test's own process: a scratch backend, the world, and every scenario in turn.

Started only by `launcher`, with a clean environment and a folder of its own. What it needs comes
on stdin as JSON, so the Claude credential never appears in an argument or the environment.
"""

import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

PROGRESS = "progress.json"
REPORT = "report.md"
RESULTS = "results.json"
STOP = "stop"  # the app asked to stop: finish this scenario, skip the rest
START_SECONDS = 120

logger = logging.getLogger(__name__)


def write_json(path: Path, value: object) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value), encoding="utf-8")
    os.replace(tmp, path)


def _run(folder: Path, job: dict[str, Any]) -> int:
    # After the environment is the host's own: these read it when imported.
    from ringkeeper.claude_auth import credential_ref  # noqa: PLC0415
    from ringkeeper.config import get_settings  # noqa: PLC0415
    from ringkeeper.db import session_scope  # noqa: PLC0415
    from ringkeeper.model_choice import router_model  # noqa: PLC0415
    from ringkeeper.models import AuthMode  # noqa: PLC0415
    from ringkeeper.scenarios import report, world  # noqa: PLC0415
    from ringkeeper.scenarios.catalog import SCENARIOS  # noqa: PLC0415
    from ringkeeper.scenarios.driver import Driver  # noqa: PLC0415
    from ringkeeper.vault import get_vault  # noqa: PLC0415

    settings = get_settings()
    vault = get_vault(settings)
    mode = AuthMode(job["credential"]["mode"])
    vault.store(credential_ref(settings, mode), job["credential"]["secret"])
    found = world.read_sandboxes(Path(job["config"]) if job.get("config") else None)
    with session_scope() as session:
        built = world.build(session, settings, vault, found)
    driver = Driver(settings, vault, built)

    only = set(job.get("only") or [])
    chosen = [s for s in SCENARIOS if not only or s.key in only]
    runnable = [s for s in chosen if not s.needs or s.needs in built.sandboxes]
    ceiling = float(job.get("ceiling") or 0)
    results: list[report.Result] = []
    spent = 0.0

    def progress(current: str | None, state: str = "running") -> None:
        s = report.summarize(results)
        write_json(
            folder / PROGRESS,
            {
                "state": state, "total": len(runnable), "done": s.ran, "current": current,
                "passed": s.passed, "failed": s.ran - s.passed, "skipped": s.skipped,
                "cost": round(spent, 2),
            },
        )  # fmt: skip

    for scenario in chosen:
        if scenario.needs and scenario.needs not in built.sandboxes:
            results.append(report.skipped(scenario, f"no {scenario.needs} in scenarios.toml"))
            continue
        if (folder / STOP).exists():
            results.append(report.skipped(scenario, "the run was stopped"))
            continue
        if ceiling and spent >= ceiling:
            results.append(report.skipped(scenario, f"stopped at the ${ceiling:.2f} suite limit"))
            continue
        progress(scenario.title)
        seen = driver.run(scenario)
        spent += seen.cost
        results.append(report.result(scenario, seen))
        logger.info("%s: %s", scenario.key, results[-1].wrong or "pass")

    day = datetime.now().astimezone().date().isoformat()
    text = report.render(
        results, day=day, model=router_model(settings), credential=job["credential"]["label"]
    )
    (folder / REPORT).write_text(text, encoding="utf-8")
    summary = report.summarize(results)
    write_json(
        folder / RESULTS,
        {"day": day, "passed": summary.passed, "ran": summary.ran, "skipped": summary.skipped},
    )
    progress(None, "finished")
    return 0


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    job: dict[str, Any] = json.loads(sys.stdin.read())

    from ringkeeper.desktop import server  # noqa: PLC0415

    ready, stop = threading.Event(), threading.Event()
    backend = threading.Thread(
        target=server.run, args=(folder / "data",), kwargs={"ready": ready, "stop": stop},
        daemon=True, name="self-test-backend",
    )  # fmt: skip
    backend.start()
    if not ready.wait(START_SECONDS):
        write_json(folder / PROGRESS, {"state": "failed", "error": "The backend didn't start."})
        return 1
    try:
        return _run(folder, job)
    except Exception as error:
        logger.exception("the self-test stopped")
        write_json(folder / PROGRESS, {"state": "failed", "error": str(error)})
        return 1
    finally:
        stop.set()
        backend.join(30)


if __name__ == "__main__":
    sys.exit(main())
