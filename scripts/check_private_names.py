"""Fail when new text names someone on a private list: a real client, say.

The list is a plain file kept outside the repository, one name per line ("#" starts a comment).
Matching ignores case and finds a name inside a longer word too. Only what a change adds is read:
added lines, new file paths and the commit messages in the range, so text already on main doesn't
fail every run. The report gives the file and line, or the commit, never the name or the line's
text, so the list stays private in CI logs too.

    python scripts/check_private_names.py --range origin/main..HEAD
    python scripts/check_private_names.py --install-hook     # check every git push from here

The list comes from --names, else RINGKEEPER_PRIVATE_NAMES_FILE, else
~/.ringkeeper-private-names.txt. With no list (or an empty one) it warns and passes: a fork's pull
request can't read the repository's secret.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_LIST = Path.home() / ".ringkeeper-private-names.txt"

HOOK = """#!/bin/sh
# Installed by scripts/check_private_names.py --install-hook: refuse a push whose new text names
# someone on the private list. A push of one branch reads the commits the remote doesn't have yet.
z=0000000000000000000000000000000000000000
script=scripts/check_private_names.py
# A branch made before the check came in: use the main checkout's copy.
[ -f "$script" ] || script="$(dirname "$(git rev-parse --git-common-dir)")/$script"
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$z" ] && continue  # a deletion adds nothing
  if [ "$remote_sha" = "$z" ]; then
    base=$(git merge-base origin/main "$local_sha" 2>/dev/null) || base=""
  else
    base="$remote_sha"
  fi
  [ -z "$base" ] && continue
  [ "$base" = "$local_sha" ] && continue
  python "$script" --range "$base..$local_sha" --strict || status=1
done
exit $status
"""


def load_names(path: Path) -> list[str]:
    if not path.is_file():
        return []
    names: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.split("#", 1)[0].strip()
        if name:
            names.append(name)
    return names


def git(*args: str) -> str:
    out = subprocess.run(  # noqa: S603 - git with this script's own arguments
        ["git", *args],  # noqa: S607 - whichever git runs the push
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return out.stdout


def find(names: list[str], base: str, head: str) -> list[str]:
    """Where a name is in what base..head adds: "path:line", a file name or a commit message."""
    pattern = re.compile("|".join(re.escape(n) for n in names), re.IGNORECASE)
    hits: list[str] = []

    diff = git(
        "diff", "--no-color", "--no-ext-diff", "--unified=0", "--no-renames", f"{base}...{head}"
    )
    path = ""
    line_no = 0
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            if path and pattern.search(path):
                hits.append(f"{path} (file name)")
        elif line.startswith("@@"):
            m = re.search(r"\+(\d+)", line)
            line_no = int(m.group(1)) if m else 0
        elif line.startswith("+") and path:
            if pattern.search(line[1:]):
                hits.append(f"{path}:{line_no}")
            line_no += 1

    log = git("log", "--format=%H%x00%B%x01", f"{base}..{head}")
    for entry in log.split("\x01"):
        sha, _, message = entry.strip("\n").partition("\x00")
        if sha and pattern.search(message):
            hits.append(f"commit {sha[:12]} (message)")
    return hits


def install_hook() -> None:
    hooks = Path(git("rev-parse", "--git-common-dir").strip()) / "hooks"
    hooks.mkdir(exist_ok=True)
    target = hooks / "pre-push"
    if target.exists() and "check_private_names" not in target.read_text(encoding="utf-8"):
        sys.exit(f"{target} exists and isn't this check's: call this script from it by hand.")
    target.write_bytes(HOOK.encode("utf-8"))
    target.chmod(0o755)
    print(f"Installed {target}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--range", help="BASE..HEAD: check what HEAD adds over BASE")
    parser.add_argument("--names", type=Path, help="the private list (one name per line)")
    parser.add_argument("--strict", action="store_true", help="fail when the list is missing")
    parser.add_argument("--install-hook", action="store_true", help="run this on every git push")
    args = parser.parse_args()

    if args.install_hook:
        install_hook()
        return 0
    if not args.range or ".." not in args.range:
        parser.error("--range BASE..HEAD is required")
    base, head = args.range.split("..", 1)
    if base.strip("0") == "":  # a first push: nothing to compare with
        print("No base to compare with: nothing to check.")
        return 0

    env_list = os.environ.get("RINGKEEPER_PRIVATE_NAMES_FILE")
    path = args.names or (Path(env_list) if env_list else DEFAULT_LIST)
    names = load_names(path)
    if not names:
        message = f"No private names to check against ({path} is missing or empty)."
        if args.strict:
            print(message, file=sys.stderr)
            return 1
        prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") else ""
        print(prefix + message + " Passing without a check.")
        return 0

    hits = find(names, base, head or "HEAD")
    if not hits:
        print(f"No private names in {base[:12]}..{(head or 'HEAD')[:12]} ({len(names)} checked).")
        return 0
    print("A name on the private list is in what this change adds:", file=sys.stderr)
    for hit in hits:
        print(f"  {hit}", file=sys.stderr)
    print("Use a made-up name there.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
