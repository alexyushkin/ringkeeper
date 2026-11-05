"""What the email connections share: who a message goes to, and when that's bulk.

One message waits for approval. Many people at once is a mailing, which needs the
connection's name typed. A test to the connection's own test address runs by itself:
the agent can't choose where it goes.
"""

from email.message import EmailMessage
from typing import Annotated, Any, cast

from pydantic import Field

from ringkeeper.adapters.base import JSON, ToolError
from ringkeeper.adapters.gmail import ADDRESS_PATTERN

BULK = 10  # more recipients than this, on one message, is a mailing
MOST = 500
Address = Annotated[str, Field(pattern=ADDRESS_PATTERN)]
Recipients = Annotated[list[Address], Field(min_length=1, max_length=MOST)]
MoreRecipients = Annotated[list[Address] | None, Field(max_length=MOST)]
Subject = Annotated[str, Field(max_length=500)]
Body = Annotated[str, Field(max_length=100_000)]
Html = Annotated[str | None, Field(description="An HTML version, if any.", max_length=500_000)]


def _count(value: Any) -> int:
    return len(cast(list[Any], value)) if isinstance(value, list) else 0


def bulk_reason(args: JSON) -> str | None:
    """Why this send needs the name typed: it goes to many people at once."""
    total = sum(_count(args.get(k)) for k in ("to", "cc", "bcc"))
    return f"sends to {total} people at once" if total > BULK else None


def message(
    sender: str,
    to: list[str],
    subject: str,
    body: str,
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    html: str | None = None,
    in_reply_to: str | None = None,
) -> EmailMessage:
    built = EmailMessage()
    built["From"] = sender
    built["To"] = ", ".join(to)
    if cc:
        built["Cc"] = ", ".join(cc)
    if bcc:
        built["Bcc"] = ", ".join(bcc)
    built["Subject"] = subject
    if in_reply_to:
        built["In-Reply-To"] = in_reply_to
        built["References"] = in_reply_to
    built.set_content(body)
    if html:
        built.add_alternative(html, subtype="html")
    return built


def own_test_address(fields: JSON) -> str:
    """Where test sends go; the owner set it, and the agent can't change it."""
    address = str(fields.get("test_address") or "").strip()
    if not address:
        raise ToolError(
            "this connection has no test address: the owner can add one on its page, and "
            "until then a test has to be sent as a normal email, with approval"
        )
    return address
