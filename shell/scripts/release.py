"""Release helpers for .github/workflows/release.yml.

    python shell/scripts/release.py version v0.1.0-beta.1   # set it; print version=...
    python shell/scripts/release.py notes 0.1.0-beta.1      # that version's CHANGELOG entry
    python shell/scripts/release.py manifest --version 0.1.0-beta.1 --artifacts DIR \\
        --base-url https://github.com/OWNER/REPO/releases/download/v0.1.0-beta.1 > latest.json

The version comes from the tag (vX.Y.Z or vX.Y.Z-pre.N) and goes into
shell/tauri.conf.json, which the app reports in Settings > About. Every release
needs its own section in CHANGELOG.md: "## X.Y.Z (YYYY-MM-DD)".
"""

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CONF = REPO / "shell" / "tauri.conf.json"
CHANGELOG = REPO / "CHANGELOG.md"
SEMVER = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$")
# The updater's names for the platforms, and which built file each one updates from.
PLATFORMS = {
    "windows-x86_64": "-setup.exe",
    "darwin-aarch64": ".app.tar.gz",
}


def notes(version: str) -> str:
    text = CHANGELOG.read_text(encoding="utf-8")
    match = re.search(
        rf"^## {re.escape(version)}\b[^\n]*\n(.*?)(?=^## |\Z)", text, re.MULTILINE | re.DOTALL
    )
    if match is None:
        sys.exit(f"CHANGELOG.md has no '## {version}' section")
    return match.group(1).strip()


def set_version(tag: str) -> str:
    version = tag.removeprefix("v")
    if not SEMVER.match(version):
        sys.exit(f"{tag!r} isn't a release tag (vX.Y.Z or vX.Y.Z-beta.N)")
    notes(version)  # a release without a changelog entry stops here
    conf = json.loads(CONF.read_text(encoding="utf-8"))
    conf["version"] = version
    CONF.write_bytes((json.dumps(conf, indent=2) + "\n").encode())  # LF, as committed
    return version


def manifest(version: str, artifacts: Path, base_url: str) -> dict:
    platforms = {}
    for platform, suffix in PLATFORMS.items():
        built = [p for p in artifacts.rglob(f"*{suffix}") if p.is_file()]
        if not built:
            continue
        [file] = built
        signature = file.with_name(file.name + ".sig")
        if not signature.is_file():
            sys.exit(f"{file.name} has no updater signature ({signature.name})")
        platforms[platform] = {
            "signature": signature.read_text(encoding="utf-8").strip(),
            "url": f"{base_url.rstrip('/')}/{file.name}",
        }
    if "windows-x86_64" not in platforms:
        sys.exit("no signed Windows installer in the artifacts")
    return {
        "version": version,
        "notes": notes(version),
        "pub_date": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "platforms": platforms,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("version").add_argument("tag")
    commands.add_parser("notes").add_argument("version")
    made = commands.add_parser("manifest")
    made.add_argument("--version", required=True)
    made.add_argument("--artifacts", type=Path, required=True)
    made.add_argument("--base-url", required=True)
    args = parser.parse_args()
    if args.command == "version":
        print(f"version={set_version(args.tag)}")
    elif args.command == "notes":
        print(notes(args.version))
    else:
        print(json.dumps(manifest(args.version, args.artifacts, args.base_url), indent=2))


if __name__ == "__main__":
    main()
