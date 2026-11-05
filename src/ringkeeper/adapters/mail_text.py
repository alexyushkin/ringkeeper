"""An email's text, the way someone would read it.

HTML becomes plain text that keeps what it said: a link keeps its address and a table keeps its
rows. The earlier messages a reply quotes are set apart, because in a long thread they can be
most of the message and say nothing new. A forward isn't a quote: what was forwarded is the
message. Nothing is thrown away: `include_quoted` puts it all back.
"""

import re
from html.parser import HTMLParser
from typing import Annotated

from pydantic import Field

from ringkeeper.adapters.base import JSON
from ringkeeper.adapters.paging import PAGE_CHARS, page

QUOTED_SHOWN = 20_000  # the rest of a long chain is read with include_quoted
THREAD_BODY_CHARS = 20_000  # of each message; read_mail has the rest
BLOCKS = frozenset({
    "address", "article", "aside", "blockquote", "center", "dd", "div", "dl", "dt", "fieldset",
    "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "main", "nav",
    "ol", "p", "pre", "section", "table", "ul",
})  # fmt: skip
HIDDEN = frozenset({"head", "script", "style", "title", "template", "noscript"})
VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta"})

IncludeQuoted = Annotated[
    bool, Field(description="Keep the earlier messages a reply quotes in the body.")
]
AttachmentId = Annotated[
    str, Field(description="The attachment's id, from the message.", min_length=1, max_length=2000)
]
ThreadOffset = Annotated[
    int, Field(ge=0, description="The message to start from. Use next_offset from the last part.")
]
ThreadLimit = Annotated[int, Field(ge=1, le=100, description="The most messages to return.")]
ThreadBody = Annotated[
    int, Field(ge=1, le=PAGE_CHARS, description="The most characters of each message's text.")
]

_REPLY_HEADER = re.compile(
    r"^\s*(?:>\s*)?(?:On\s.{1,200}\swrote:|Am\s.{1,200}\sschrieb.{0,30}:|Le\s.{1,200}\sa\s[ée]crit\s?:"
    r"|El\s.{1,200}\sescribi[óo]:|-{2,}\s*Original Message\s*-{2,})\s*$",
    re.IGNORECASE,
)
_FORWARD = re.compile(
    r"^\s*(?:-{2,}\s*Forwarded message\s*-{2,}|Begin forwarded message:)\s*$", re.IGNORECASE
)
_OUTLOOK_FROM = re.compile(r"^\s*\*?(?:From|De|Von):\*?\s+\S", re.IGNORECASE)
_OUTLOOK_NEXT = re.compile(
    r"^\s*\*?(?:Sent|Date|Envoy[ée]|Gesendet|Enviado):\*?\s+\S", re.IGNORECASE
)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.hidden = 0
        self.link: str | None = None
        self.link_text = ""
        self.quote = 0
        self.cells = 0
        self.pre = 0

    def _line(self) -> None:
        self.out.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        if tag in HIDDEN and tag not in VOID:
            self.hidden += 1
        if self.hidden:
            return
        if tag == "blockquote":
            self.quote += 1
        if tag in BLOCKS or tag in ("br", "tr"):
            self._line()
        self._inline(tag, fields)

    def _inline(self, tag: str, fields: dict[str, str | None]) -> None:
        match tag:
            case "tr":
                self.cells = 0
            case "td" | "th":
                if self.cells:
                    self.out.append(" | ")
                self.cells += 1
            case "li":
                self._line()
                self.out.append("- ")
            case "a":
                self.link = fields.get("href") or None
                self.link_text = ""
            case "img":
                if alt := (fields.get("alt") or "").strip():
                    self.out.append(f"[{alt}]")
            case "pre":
                self.pre += 1
            case _:
                pass

    def handle_endtag(self, tag: str) -> None:
        if tag in HIDDEN and tag not in VOID:
            self.hidden = max(self.hidden - 1, 0)
            return
        if self.hidden:
            return
        if tag == "a" and self.link:
            shown = self.link_text.strip()
            if self.link.lower().startswith(("http:", "https:", "mailto:")) and (
                self.link.removeprefix("mailto:") not in shown
            ):
                self.out.append(f" ({self.link})")
            self.link = None
        if tag == "pre":
            self.pre = max(self.pre - 1, 0)
        if tag == "blockquote":
            self.quote = max(self.quote - 1, 0)
        if tag in BLOCKS:
            self._line()

    def handle_data(self, data: str) -> None:
        if self.hidden:
            return
        text = data if self.pre else re.sub(r"\s+", " ", data)
        if self.link is not None:
            self.link_text += text
        if self.quote and text.strip():
            mark = "> " * self.quote
            text = "\n".join(mark + line for line in text.splitlines())
        self.out.append(text)


def html_to_text(html: str) -> str:
    """Readable text from an email's HTML, keeping links and tables."""
    parser = _Text()
    parser.feed(html)
    parser.close()
    lines = [re.sub(r"[ \t]+$", "", line) for line in "".join(parser.out).splitlines()]
    text = "\n".join(line.lstrip(" ") if not line.startswith("  ") else line for line in lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def best_text(plain: str, html: str) -> str:
    """The plain version, unless it's a stub ("view this in your browser") of the HTML one."""
    readable = html_to_text(html) if html else ""
    return readable if len(readable) > 2 * len(plain.strip()) else plain.strip()


def _chain_start(lines: list[str]) -> int | None:
    """The first line of an earlier message: "On ... wrote:" or an Outlook header block."""
    for number, line in enumerate(lines):
        if _FORWARD.match(line):
            return None
        if _REPLY_HEADER.match(line):
            return number
        following = lines[number + 1] if number + 1 < len(lines) else ""
        if line.lstrip().startswith("________") and _OUTLOOK_FROM.match(following):
            return number
        if _OUTLOOK_FROM.match(line) and any(
            _OUTLOOK_NEXT.match(n) for n in lines[number + 1 : number + 4]
        ):
            return number
    return None


def split_quoted(text: str) -> tuple[str, str]:
    """(what's new, what it quotes). A reply written between quoted lines stays whole: only a
    chain at the end moves, and a message that's nothing but a chain is left as it is."""
    lines = text.splitlines()
    cut = _chain_start(lines)
    if cut is None:
        cut = len(lines)
        while cut > 0 and (not lines[cut - 1].strip() or lines[cut - 1].lstrip().startswith(">")):
            cut -= 1
    new = "\n".join(lines[:cut]).strip()
    quoted = "\n".join(lines[cut:]).strip()
    return (new, quoted) if new else (text.strip(), "")


def thread_page(ordered: list[JSON], offset: int, limit: int) -> JSON:
    """Some of a conversation's messages, oldest first, saying which."""
    chosen = ordered[offset : offset + limit]
    end = offset + len(chosen)
    more = end < len(ordered)
    answer: JSON = {
        "messages": chosen,
        "total_messages": len(ordered),
        "offset": offset,
        "returned_messages": len(chosen),
        "truncated": more or offset > 0,
        "next_offset": end if more else None,
    }
    if more:
        answer["note"] = "More messages follow. Read from next_offset before answering."
    return answer


def body_fields(
    text: str, offset: int = 0, limit: int = PAGE_CHARS, include_quoted: bool = False
) -> JSON:
    """A message's body as a tool answers it: the part asked for, what part that is, and the
    quoted chain apart from it unless it was asked for."""
    if include_quoted:
        return page(text.strip(), offset, limit, field="body")
    new, quoted = split_quoted(text)
    answer = page(new, offset, limit, field="body")
    if quoted:
        answer |= {
            "quoted": quoted[:QUOTED_SHOWN],
            "quoted_chars": len(quoted),
            "quoted_truncated": len(quoted) > QUOTED_SHOWN,
        }
    return answer
