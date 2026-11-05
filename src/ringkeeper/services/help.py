"""The Help assistant: "how do I…?" answered from this version's guide, and your setup.

The guide ships in the app (help_index.json, built from docs/), section by section. A
question finds its sections here, with no model. Then:

- A section of "What Ringkeeper doesn't do yet" matches: it says so, with no model.
- The question names something the guide never mentions: it says it has no docs on
  that. Nothing to go on means nothing for a model to invent from.
- Otherwise one small model call answers from those sections and Ringkeeper's own
  records (read only), and the answer cites its sections.

It ends with buttons for the next step: links into the app, nothing that changes
anything by itself. The router sends it these questions; so do Help > Ask and the
"?" hints.
"""

import json
import re
from dataclasses import asdict, dataclass, field
from functools import cache
from importlib import resources
from typing import Any

from sqlalchemy.orm import Session

from ringkeeper.config import Settings
from ringkeeper.model_choice import router_model
from ringkeeper.router.model import RouterModel
from ringkeeper.router.self_answer import snapshot
from ringkeeper.services import clients

GUIDE = "https://github.com/alexyushkin/ringkeeper/blob/main/docs"
NOT_YET = "What Ringkeeper doesn't do yet"
NOT_DONE = "That's not something Ringkeeper does yet."
NO_DOCS = "I don't have docs on that."
SUGGEST = {
    "label": "Suggest a feature",
    "url": "https://github.com/alexyushkin/ringkeeper/issues/new",
}
REPORT = {"label": "Report a problem", "href": "/help?report=1"}
MAX_SECTIONS = 5

SYSTEM_PROMPT = f"""\
You are Ringkeeper's help. You answer how to use Ringkeeper, from GUIDE below and
RINGKEEPER DATA (the operator's own setup). Those are everything you know.

- Answer only from them. Never invent a feature, button, page or setting.
- If they don't cover it, answer exactly "{NO_DOCS}" and nothing else.
- Use the data to be specific: "Seaside has no projects yet, so first add one."
- Name buttons and pages as the guide does. You can't change anything yourself.
- A few short sentences or a short numbered list.
- End with a last line "SOURCES: " and the numbers of the guide sections you used.
"""

_HOW = re.compile(
    r"^\s*(how\b(?! (many|much)\b)|can (i|you|ringkeeper|it|we)\b|could (i|you|ringkeeper)\b|"
    r"where (do|can|is|are|does)\b|what('s| is| are) (a|an|the difference)\b|is there\b|"
    r"does ringkeeper\b|do i\b|why (do|does|is|can)\b|what happens\b)",
    re.I,
)
_ASKS_HOW = re.compile(
    r"^\s*(how (do|can|should) (i|we)\b|where (do|can) (i|we)\b|where (is|are) (my|the)\b|"
    r"what('s| is) the difference\b|can ringkeeper\b|does ringkeeper\b|how does ringkeeper\b|"
    r"is there an? (\w+ )?app\b)",
    re.I,
)
_TOPICS = re.compile(
    r"\b(connections?|connectors?|projects?|clients?|agents?|routines?|approvals?|backups?|"
    r"restore|undo|revert|tools?|subscription|google|tour|ringkeeper|settings?|digest|"
    r"hotkey|shortcuts?|mentions?|app)\b",
    re.I,
)
_CONNECTS = re.compile(r"\b(connect|link|add|install|hook|integrate|use)\b", re.I)
_STOP = frozenset(
    [
        "a",
        "able",
        "about",
        "again",
        "all",
        "also",
        "am",
        "an",
        "and",
        "another",
        "any",
        "anyway",
        "app",
        "are",
        "as",
        "at",
        "be",
        "been",
        "but",
        "by",
        "can",
        "could",
        "did",
        "do",
        "does",
        "doing",
        "easily",
        "first",
        "for",
        "from",
        "get",
        "go",
        "got",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "like",
        "made",
        "make",
        "me",
        "more",
        "my",
        "need",
        "new",
        "no",
        "not",
        "of",
        "on",
        "one",
        "or",
        "our",
        "out",
        "please",
        "possible",
        "quickly",
        "really",
        "ringkeeper",
        "second",
        "should",
        "so",
        "some",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "thing",
        "things",
        "this",
        "to",
        "up",
        "us",
        "use",
        "used",
        "using",
        "want",
        "was",
        "way",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "work",
        "working",
        "works",
        "would",
        "you",
        "your",
    ]
)


@dataclass(frozen=True)
class Section:
    doc: str
    anchor: str
    title: str
    text: str

    @property
    def url(self) -> str:
        return f"{GUIDE}/{self.doc}.md" + (f"#{self.anchor}" if self.anchor else "")


@dataclass
class Help:
    text: str
    cites: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    actions: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    cost: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def is_how_to(question: str) -> bool:
    return bool(_HOW.search(question))


def asks_how(question: str, names: list[str]) -> bool:
    """A plain "how do I…?" about Ringkeeper itself, naming no client or project. The
    router's model decides too; this catches what it might send to a project anyway."""
    lowered = question.lower()
    if any(re.search(rf"(?<![\w-]){re.escape(n.lower())}(?![\w-])", lowered) for n in names if n):
        return False
    return bool(_ASKS_HOW.search(question) and _TOPICS.search(question))


@cache
def sections() -> tuple[Section, ...]:
    raw = resources.files("ringkeeper").joinpath("help_index.json").read_text(encoding="utf-8")
    return tuple(Section(**s) for s in json.loads(raw))


def _stem(word: str) -> str:
    """Enough to match "changes", "changed" and "changing"."""
    for ending in ("ing", "ies", "ed", "s"):
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            word = word[: -len(ending)] + ("y" if ending == "ies" else "")
            break
    return word.removesuffix("e") if len(word) > 3 else word


def words(text: str) -> list[str]:
    found = re.findall(r"[a-z0-9]+", text.lower().replace("'", ""))
    return [_stem(w) for w in found if w not in _STOP and len(w) > 1]


@cache
def _vocabulary() -> frozenset[str]:
    return frozenset(w for s in sections() for w in words(f"{s.title} {s.text}"))


@cache
def _rarity() -> dict[str, float]:
    """How much a word says: the fewer sections have it, the more."""
    counts: dict[str, int] = {}
    for section in sections():
        for word in set(words(f"{section.title} {section.text}")):
            counts[word] = counts.get(word, 0) + 1
    total = len(sections())
    return {w: 1.0 + (total / n) ** 0.5 for w, n in counts.items()}


def search(question: str, limit: int = MAX_SECTIONS) -> list[Section]:
    """The guide's best sections for a question, best first."""
    wanted = set(words(question))
    rarity = _rarity()
    scored: list[tuple[float, int, Section]] = []
    for position, section in enumerate(sections()):
        title = set(words(section.title))
        body = set(words(section.text))
        score = sum(
            rarity.get(w, 0.0) * (3.0 if w in title else 1.0) for w in wanted if w in title | body
        )
        if score:
            scored.append((score, -position, section))
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    return [s for _, _, s in scored[:limit]]


def unknown(question: str, names: list[str]) -> list[str]:
    """What a question names that the guide and your setup never mention."""
    known = _vocabulary() | frozenset(w for n in names for w in words(n.replace("-", " ")))
    return [w for w in words(question) if w not in known]


def _not_yet_line(section: Section, question: str) -> str | None:
    """The line of "doesn't do yet" this question is about, if it's about one."""
    wanted = set(words(question))
    for line in re.split(r"(?=\*\*|- \*\*)", section.text):
        if wanted & set(words(line)) - {"ringkeeper"}:
            text = line.lstrip("- ").replace("**", "").strip()
            return text[:1].upper() + text[1:]
    return None


# The next step, by guide page. Each opens a page; none changes anything by itself.
ACTIONS: dict[str, list[dict[str, str]]] = {
    "connections": [{"label": "Browse connectors", "href": "/connections/new"}],
    "services/google": [
        {"label": "Open Settings \u203a Developer apps", "href": "/settings#developer-apps"}
    ],
    "services/add-on-tools": [
        {"label": "Add your own tool", "href": "/connections/new?kind=shared"}
    ],
    "agents-and-routines": [
        {"label": "Open Agents", "href": "/agents"},
        {"label": "Open Routines", "href": "/routines"},
    ],
    "approvals": [{"label": "Open Approvals", "href": "/approvals"}],
    "undo": [{"label": "Open Tasks", "href": "/tasks"}],
    "backup": [{"label": "Open Settings \u203a Backup", "href": "/settings#backup"}],
    "notifications": [
        {"label": "Open Settings \u203a Notifications", "href": "/settings#notifications"}
    ],
    "working-faster": [{"label": "Open Settings \u203a Advanced", "href": "/settings#advanced"}],
    "concepts": [{"label": "Start the tour", "href": "/?tour=1"}],
}


def _actions(cited: list[Section], data: dict[str, Any], question: str) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    lowered = question.lower()
    # A client this asks about that has nothing yet: the first step is a project.
    for slug, name in data["clients_with_no_projects"].items():
        if name.lower() in lowered:
            found.append({"label": "Add project", "href": f"/projects/new?client={slug}"})
    if "subscription" in lowered or "api key" in lowered:
        found.append({"label": "Open Settings \u203a Claude", "href": "/settings#claude"})
    for section in cited:
        found += ACTIONS.get(section.doc, [])
    unique = {a["label"]: a for a in found}
    return list(unique.values())[:3]


def _cite(section: Section) -> dict[str, str]:
    return {"title": section.title, "url": section.url}


def _sources(text: str, shown: list[Section]) -> tuple[str, list[Section]]:
    """The answer without its SOURCES line, and the sections that line names."""
    match = re.search(r"\n?\s*SOURCES:\s*([\d,\s]*)\s*$", text)
    if match is None:
        return text.strip(), shown[:1]
    numbers = [int(n) for n in re.findall(r"\d+", match[1])]
    used = [shown[n - 1] for n in numbers if 0 < n <= len(shown)]
    return text[: match.start()].strip(), used or shown[:1]


def answer(session: Session, settings: Settings, model: RouterModel, question: str) -> Help:
    data = snapshot(session, settings)
    busy = {str(p["client"]) for p in data["projects"]}
    data["clients_with_no_projects"] = {
        slug: name for slug, name in clients.names(session).items() if slug not in busy
    }
    names = [
        *data["clients_with_no_projects"].values(),
        *(str(c["name"]) for c in data["clients"]),
        *(str(p["name"]) for p in data["projects"]),
        *(str(c["name"]) for c in data["connections"]),
    ]
    found = search(question)
    missing = unknown(question, names)
    top = found[0] if found else None
    if top is not None and top.title == NOT_YET and (line := _not_yet_line(top, question)):
        return Help(f"{NOT_DONE} {line}", [_cite(top)], [SUGGEST, REPORT])
    # Connecting something the guide doesn't name is still connecting: Other connectors.
    if (missing and not _CONNECTS.search(question)) or top is None:
        return Help(
            f"{NO_DOCS} If you'd like Ringkeeper to do this, say so:", [], [SUGGEST, REPORT]
        )
    guide = "\n\n".join(f"[{n}] {s.title} ({s.doc}): {s.text}" for n, s in enumerate(found, 1))
    prompt = (
        f"GUIDE:\n{guide}\n\nRINGKEEPER DATA:\n{json.dumps(data, indent=1, default=str)}\n\n"
        f"QUESTION:\n{question}"
    )
    completion = model.complete(SYSTEM_PROMPT, prompt, router_model(settings))
    text, used = _sources(completion.text.strip(), found)
    cost = completion.cost_usd or 0.0
    if not text or text.startswith(NO_DOCS):
        return Help(NO_DOCS, [], [SUGGEST, REPORT], cost)
    return Help(text, [_cite(s) for s in used], _actions(used, data, question), cost)
