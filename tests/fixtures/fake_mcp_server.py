"""A tiny MCP server over stdio, standing in for one from Claude Desktop's settings.

It says which secrets it was given without ever printing them: the length of
FAKE_API_KEY and its first argument, and whether a Claude credential leaked in.
"""

import os
import sys

from fastmcp import FastMCP

server = FastMCP("fake-notes")
NOTES = ["first note"]


@server.tool
def read_notes() -> list[str]:
    """List the notes."""
    return NOTES


@server.tool
def delete_note(index: int) -> str:
    """Delete a note by its position."""
    return f"deleted note {index}"


@server.tool
def what_i_got() -> dict[str, object]:
    """What this server was started with (lengths, not values)."""
    return {
        "api_key_length": len(os.environ.get("FAKE_API_KEY", "")),
        "first_arg_length": len(sys.argv[1]) if len(sys.argv) > 1 else 0,
        "claude_credential": "ANTHROPIC_API_KEY" in os.environ,
        "ringkeeper_env": sorted(k for k in os.environ if k.startswith("RINGKEEPER_")),
        "cwd": os.getcwd(),
    }


if __name__ == "__main__":
    server.run(show_banner=False)
