"""Signed links: a web address that works for whoever holds it.

A Lemon Squeezy customer portal link, an S3 or Google Cloud presigned URL, an Azure SAS link: the
signature in the address is the permission, so the address is as good as a password until it
expires. `mask` keeps where it points and takes out the rest, wherever anything is kept: logs,
audit rows, a task's events and conversations (ringkeeper.scrub applies it wherever a secret is
scrubbed). A tool call that carries one is refused, because a held call keeps its arguments.
"""

import json
import re

MASK = "[signed link removed]"

# Also inside a JSON string (\/ and &) or another address (%3F, %26, %3D).
_LINK = re.compile(r"""https?:(?:\\*/){2}(?:[^\s"'<>\\`]|\\+/|\\+u0026)+""", re.IGNORECASE)
_START = r"(?:[?&;]|%3F|%26|\\+u0026)"
_EQUALS = r"(?:=|%3D)"
_SIGNED = re.compile(
    rf"{_START}(?:amp;)?(?:signature|sig|x-amz-signature|x-goog-signature|x-amz-security-token"
    rf"|x-ms-signature|tempauth|hmac|token_hash){_EQUALS}",
    re.IGNORECASE,
)
_EXPIRES = re.compile(rf"{_START}(?:amp;)?(?:expires|expiry|exp|e){_EQUALS}", re.IGNORECASE)
_TOKEN = re.compile(rf"{_START}(?:amp;)?(?:token|hash|key|auth){_EQUALS}", re.IGNORECASE)
_QUERY = re.compile(r"\?|%3F", re.IGNORECASE)
TRAILING = ".,;:!)]}"


def _is_signed(link: str) -> bool:
    return bool(_SIGNED.search(link) or (_EXPIRES.search(link) and _TOKEN.search(link)))


def _masked(found: re.Match[str]) -> str:
    link = found.group()
    kept = link.rstrip(TRAILING)
    tail = link[len(kept) :]
    if not _is_signed(kept):
        return link
    query = _QUERY.search(kept)
    return f"{kept[: query.start()] if query else kept}?{MASK}{tail}"


def mask(text: str) -> str:
    """The text with every signed link cut down to where it points."""
    if "http" not in text and "HTTP" not in text:
        return text
    return _LINK.sub(_masked, text)


def found(value: object) -> bool:
    """Whether a JSON-like value holds a signed link anywhere."""
    text = json.dumps(value, default=str, ensure_ascii=False)
    return mask(text) != text
