"""Pin the Claude Code version that Docker tasks fetch on first start.

    uv run python scripts/pin_claude_code.py          # the version the SDK bundles
    uv run python scripts/pin_claude_code.py 2.1.310  # or a named one

Writes worker/ringkeeper_worker/claude-code.json with each Linux package's tarball and
sha512 from the npm registry. Run it with each release: the tests check the pin matches
the CLI the desktop app ships, and a version must be at least two weeks old.
"""

import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

PIN = Path(__file__).resolve().parents[1] / "worker" / "ringkeeper_worker" / "claude-code.json"
REGISTRY = "https://registry.npmjs.org"
ARCHITECTURES = {"x64": "linux-x64", "arm64": "linux-arm64"}
MIN_AGE = timedelta(days=14)


def _get(url: str) -> dict[str, object]:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (the npm registry)
        return json.load(response)


def main() -> int:
    if len(sys.argv) > 1:
        version = sys.argv[1]
    else:
        from claude_agent_sdk._cli_version import __cli_version__  # noqa: PLC0415

        version = __cli_version__
    packages: dict[str, dict[str, str]] = {}
    for arch, suffix in ARCHITECTURES.items():
        name = f"@anthropic-ai/claude-code-{suffix}"
        info = _get(f"{REGISTRY}/{name}")
        published = datetime.fromisoformat(str(info["time"][version]).replace("Z", "+00:00"))  # type: ignore[index]
        if datetime.now(UTC) - published < MIN_AGE:
            print(f"{name}@{version} is less than two weeks old ({published:%Y-%m-%d}).")
            return 1
        dist = info["versions"][version]["dist"]  # type: ignore[index]
        packages[arch] = {"tarball": dist["tarball"], "integrity": dist["integrity"]}
    PIN.write_text(
        json.dumps({"version": version, "packages": packages}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",  # as committed, on Windows too
    )
    print(f"Pinned Claude Code {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
