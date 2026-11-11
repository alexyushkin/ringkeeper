"""A made-up service for the self-test: a local MCP server, started like any imported one.

It stands in for what would otherwise need a real account: a mailbox with one very long email
and one whose attachment can't be read, and a CRM on a plan that doesn't include exports. Run as
a script, so it needs only fastmcp.
"""

import sys
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

LINES = 9000  # past the point where a result is paged, so the end needs read_more
READ_ONLY = {"readOnlyHint": True}
# As catalog's UNREADABLE_SUBJECT and UNREADABLE_FILE (this runs without Ringkeeper), and the
# answer is the one Ringkeeper's own reading gives (adapters.extract).
UNREADABLE_SUBJECT = "Spring catalogue"
UNREADABLE_FILE = "spring-catalogue.numbers"
CANT_READ = (
    f"'{UNREADABLE_FILE}' is a Numbers spreadsheet: export it as .xlsx or PDF. Text can't be "
    "taken out of that here."
)


def long_email(end: str) -> str:
    body = [
        f"Line {n}: item {n}, unit price {n % 90 + 10}.00, minimum order 12." for n in range(LINES)
    ]
    return "\n".join(["Supplier price list", *body, f"Reference code: {end}"])


def mail(end: str) -> FastMCP:
    server: FastMCP = FastMCP("Demo mail")

    @server.tool(annotations=READ_ONLY)
    def list_emails() -> list[dict[str, Any]]:  # pyright: ignore[reportUnusedFunction]
        """The inbox, newest first: each email's subject, sender and when it arrived."""
        return [
            {"subject": "Supplier price list", "from": "orders@supplier.example",
             "received": "2026-11-06T09:12:00Z"},
            {"subject": "Lunch on Friday?", "from": "ana@democo.example",
             "received": "2026-11-05T16:40:00Z"},
            {"subject": UNREADABLE_SUBJECT, "from": "sales@supplier.example",
             "received": "2026-11-04T08:05:00Z", "attachments": [UNREADABLE_FILE]},
        ]  # fmt: skip

    @server.tool(annotations=READ_ONLY)
    def read_email(subject: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """One email's whole text, by its subject. Its attachments are read with
        read_attachment."""
        if UNREADABLE_SUBJECT.lower() in subject.lower():
            return (
                f"Hello, our spring prices, the oak cutting board's among them, are in the "
                f"attached {UNREADABLE_FILE}. Attachments: {UNREADABLE_FILE}"
            )
        return long_email(end) if "price" in subject.lower() else "Lunch on Friday works for me."

    @server.tool(annotations=READ_ONLY)
    def read_attachment(subject: str, name: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """The text of an email's attachment, by the email's subject and the file's name."""
        if UNREADABLE_SUBJECT.lower() in subject.lower() and name == UNREADABLE_FILE:
            raise ToolError(CANT_READ)
        raise ToolError(f"that email has no attachment called {name}")

    return server


def crm() -> FastMCP:
    server: FastMCP = FastMCP("Demo CRM")

    @server.tool(annotations=READ_ONLY)
    def find_contact(name: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """A contact, by name."""
        return f"ana-crm-record: {name}, Lisbon"

    @server.tool(annotations=READ_ONLY)
    def export_contacts() -> str:  # pyright: ignore[reportUnusedFunction]
        """Every contact, as CSV."""
        raise ToolError("Your current plan doesn't include contact exports. Upgrade to export.")

    return server


if __name__ == "__main__":
    kind, *rest = sys.argv[1:]
    (mail(rest[0]) if kind == "mail" else crm()).run(show_banner=False)
