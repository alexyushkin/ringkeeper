"""A made-up service for the self-test: a local MCP server, started like any imported one.

It stands in for what would otherwise need a real account: a mailbox with one very long email,
and a CRM on a plan that doesn't include exports. Run as a script, so it needs only fastmcp.
"""

import sys

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError

LINES = 9000  # past the point where a result is paged, so the end needs read_more
READ_ONLY = {"readOnlyHint": True}


def long_email(end: str) -> str:
    body = [
        f"Line {n}: item {n}, unit price {n % 90 + 10}.00, minimum order 12." for n in range(LINES)
    ]
    return "\n".join(["Supplier price list", *body, f"Reference code: {end}"])


def mail(end: str) -> FastMCP:
    server: FastMCP = FastMCP("Demo mail")

    @server.tool(annotations=READ_ONLY)
    def list_emails() -> list[dict[str, str]]:  # pyright: ignore[reportUnusedFunction]
        """The inbox, newest first: each email's subject, sender and when it arrived."""
        return [
            {"subject": "Supplier price list", "from": "orders@supplier.example",
             "received": "2026-11-06T09:12:00Z"},
            {"subject": "Lunch on Friday?", "from": "ana@democo.example",
             "received": "2026-11-05T16:40:00Z"},
        ]  # fmt: skip

    @server.tool(annotations=READ_ONLY)
    def read_email(subject: str) -> str:  # pyright: ignore[reportUnusedFunction]
        """One email's whole text, by its subject."""
        return long_email(end) if "price" in subject.lower() else "Lunch on Friday works for me."

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
