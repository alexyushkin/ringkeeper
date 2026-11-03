"""Build the Help assistant's index: the user guide, section by section.

    uv run python scripts/build_help_index.py

Writes src/ringkeeper/help_index.json, which ships in the app, so the assistant answers
from the docs of the version you're running. A test fails if it's out of date.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "src" / "ringkeeper" / "help_index.json"
# The user guide. Not the notes for developers and releases.
PAGES = (
    "concepts", "connections", "agents-and-routines", "approvals", "undo", "notifications",
    "backup", "working-faster", "security", "troubleshooting", "faq", "install-desktop",
    "services/add-on-tools", "services/airtable", "services/database", "services/github",
    "services/gmail", "services/google", "services/n8n", "services/twilio", "services/vercel",
)  # fmt: skip
QUESTION = re.compile(r"^\*\*(.+?\?)\*\*\s*(.*)$", re.S)


def anchor(title: str) -> str:
    """GitHub's link to a heading."""
    return re.sub(r"[^a-z0-9 -]", "", title.lower()).strip().replace(" ", "-")


def plain(text: str) -> str:
    """Markdown down to its words: links keep their text."""
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def sections(page: str, text: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    parts = re.split(r"^(#{1,3} .+)$", text, flags=re.M)
    title, head = "", ""
    for part in parts:
        if re.match(r"^#{1,3} ", part):
            title = part.lstrip("#").strip()
            head = head or title
            continue
        body = plain(part)
        if not body:
            continue
        # A page of questions: each is its own section.
        answers = [QUESTION.match(p.strip()) for p in body.split("\n\n")]
        if answers and all(answers):
            found += [
                {"doc": page, "anchor": "", "title": a[1], "text": " ".join(a[2].split())}
                for a in answers
                if a
            ]
            continue
        found.append({"doc": page, "anchor": anchor(title) if title != head else "",
                      "title": title, "text": " ".join(body.split())})  # fmt: skip
    return found


def build() -> str:
    index = [
        s
        for page in PAGES
        for s in sections(page, (ROOT / "docs" / f"{page}.md").read_text(encoding="utf-8"))
    ]
    return json.dumps(index, indent=0, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    TARGET.write_text(build(), encoding="utf-8", newline="\n")
    print(
        f"{TARGET}: {len(json.loads(TARGET.read_text(encoding='utf-8')))} sections", file=sys.stderr
    )
