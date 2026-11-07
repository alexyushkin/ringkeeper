"""Settings > Advanced > Self-test: one run at a time, followed from the app.

The run's folder goes when the run ends; its report stays beside it, by day. An app that quits
during a run leaves the folder behind, which the next run clears: what it holds is locked by a key
that went with the run.
"""

import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ringkeeper.config import Settings
from ringkeeper.errors import RuleViolationError
from ringkeeper.scenarios import launcher
from ringkeeper.scenarios.catalog import SCENARIOS
from ringkeeper.scenarios.host import STOP
from ringkeeper.vault import Vault

FOLDER = "self-test"
RUN = "run"
CONFIG = "scenarios.toml"


def _base(settings: Settings) -> Path:
    return (settings.data_dir / FOLDER).absolute()


class SelfTest:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self._failed: str | None = None

    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def start(self, settings: Settings, vault: Vault) -> None:
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                raise RuleViolationError("The self-test is already running.")
            base = _base(settings)
            folder = base / RUN
            launcher.remove(folder)
            config = base / CONFIG
            self._failed = None
            self._process = launcher.start(
                settings, vault, folder, config=config if config.is_file() else None
            )
            process = self._process
        threading.Thread(
            target=self._finish, args=(process, folder), daemon=True, name="rk-self-test"
        ).start()

    def _finish(self, process: subprocess.Popen[bytes], folder: Path) -> None:
        process.wait()
        text = launcher.report_text(folder)
        if text is None:
            now = launcher.progress(folder) or {}
            with self._lock:
                self._failed = str(now.get("error") or "It stopped before it finished.")
        else:
            day = datetime.now().astimezone().date().isoformat()
            (folder.parent / f"{day}.md").write_text(text, encoding="utf-8")
        launcher.remove(folder)

    def stop(self, settings: Settings) -> None:
        """Finish the scenario under way, and skip the rest."""
        folder = _base(settings) / RUN
        if self.running():
            (folder / STOP).touch()

    def status(self, settings: Settings) -> dict[str, Any]:
        base = _base(settings)
        reports = sorted(base.glob("*.md")) if base.is_dir() else []
        last = reports[-1] if reports else None
        with self._lock:
            failed = self._failed
        running = self.running()
        return {
            "running": running,
            "scenarios": len([s for s in SCENARIOS if not s.needs]),
            "progress": launcher.progress(base / RUN) if running else None,
            "failed": failed,
            "report": last.read_text(encoding="utf-8") if last else None,
            "report_day": last.stem if last else None,
        }


self_test = SelfTest()
