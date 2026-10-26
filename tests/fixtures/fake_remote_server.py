"""A stand-in for a remote service's MCP server shaped like Neon's: most tools take
a project_id (and some a branch_id), a few act on the whole account. Each tool
answers with the IDs it was called with, so tests see what the gateway pinned."""

from fastmcp import FastMCP

server = FastMCP("fake-neon")
READ = {"readOnlyHint": True}


@server.tool(annotations=READ)
def list_projects() -> list[str]:
    """Every project in the account."""
    return ["p-alpha", "p-beta"]


@server.tool(annotations=READ)
def describe_project(project_id: str | None = None) -> dict[str, str | None]:
    """One project."""
    return {"project_id": project_id}


@server.tool(annotations=READ)
def describe_branch(project_id: str, branch_id: str | None = None) -> dict[str, str | None]:
    """One branch of a project."""
    return {"project_id": project_id, "branch_id": branch_id}


@server.tool(annotations={"readOnlyHint": False, "destructiveHint": True})
def run_sql(
    sql: str, project_id: str | None = None, branch_id: str | None = None
) -> dict[str, str | None]:
    """Run SQL on a project's branch."""
    return {"sql": sql, "project_id": project_id, "branch_id": branch_id}


@server.tool(annotations=READ)
def search_docs(query: str) -> str:
    """Search the service's documentation."""
    return f"docs about {query}"


if __name__ == "__main__":
    server.run(show_banner=False)
