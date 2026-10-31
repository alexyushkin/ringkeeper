"""Services with an official remote MCP server: how to connect each one, and how
far a project's link to it can be narrowed.

Each becomes an `mcp` connection whose secret names the server's URL with
either OAuth tokens (signed in with the service's own page, using MCP's
dynamic client registration: services.mcp_oauth) or a pasted token. Sign-in
is offered only where any app may register itself; the rest take a token.

Scope: a project's link to a connection can pin IDs (a Neon project and
branch, an Airtable base). The gateway checks them by argument name, never by
tool name, since servers rename tools (see `narrow`):

- a call that names another ID is refused;
- a call that leaves one out gets it filled in, when the tool takes it;
- a tool that takes none of a field's arguments acts on the whole account, so
  it's refused while that field is set (docs and search tools excepted).
"""

import itertools
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ScopeField:
    key: str  # what the link stores: {"project": "..."}
    label: str
    args: tuple[str, ...]  # the argument names that carry it
    hint: str = ""


@dataclass(frozen=True)
class Remote:
    key: str
    name: str
    url: str
    signin: bool  # MCP OAuth with dynamic client registration works for any app
    paste: str | None  # the token to paste instead of signing in (None: sign-in only)
    paste_url: str = ""  # where to make one
    paste_steps: str = ""
    auth_scheme: str = "Bearer"  # Authorization: <scheme> <token>
    scope: tuple[ScopeField, ...] = ()
    # Tools that are fine on a scoped link though they take no scope argument.
    open_tools: frozenset[str] = frozenset()
    # Tools that run SQL: checked like a database connection's (destructive.sql_reason).
    sql_tools: frozenset[str] = frozenset()
    # Tools known to only read (whatever the server's annotations say).
    read_tools: frozenset[str] = frozenset()
    # Tools that call any API endpoint, named in one of these arguments: each call is
    # decided by the endpoint's name (get/list/retrieve run, delete needs the name typed,
    # anything else waits for approval). See adapters.mcp.call_rule.
    endpoint_tools: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )
    note: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict[str, Any])
    # On the Recommended shelf. Off: kept only for connections made before (GitHub is now
    # its own connection type, with GitHub Enterprise Server and pushes).
    shelf: bool = True


CATALOG: tuple[Remote, ...] = (
    Remote(
        "neon", "Neon", "https://mcp.neon.tech/mcp", signin=True,
        paste="Neon API key", paste_url="https://console.neon.tech/app/settings/api-keys",
        paste_steps="Create an API key, ideally one limited to this client's organization.",
        scope=(
            ScopeField("project", "Neon project", ("project_id", "projectId"),
                       "The project ID, like summer-sun-123456 (Project settings)."),
            ScopeField("branch", "Branch", ("branch_id", "branchId"),
                       "Optional: a branch ID (br-...). Leave empty for any branch."),
        ),
        open_tools=frozenset({"search_docs", "get_doc", "list_regions"}),
        sql_tools=frozenset({"run_sql", "run_sql_transaction", "explain_sql_statement"}),
        note="SQL runs as the database owner unless you pick a role when you sign in.",
    ),
    Remote(
        "supabase", "Supabase", "https://mcp.supabase.com/mcp", signin=True,
        paste="Supabase access token",
        paste_url="https://supabase.com/dashboard/account/tokens",
        paste_steps="Create a personal access token.",
        scope=(
            ScopeField("project", "Supabase project", ("project_id", "projectId", "project_ref"),
                       "The project's reference ID (Project settings > General)."),
        ),
        open_tools=frozenset({"search_docs"}),
        sql_tools=frozenset({"execute_sql", "apply_migration"}),
        note="Supabase's server is in beta. Signing in picks one organization.",
    ),
    Remote(
        "airtable", "Airtable", "https://mcp.airtable.com/mcp", signin=True,
        paste="Airtable personal access token", paste_url="https://airtable.com/create/tokens",
        paste_steps="Create a token with the scopes you want, and add only this client's bases.",
        scope=(
            ScopeField("base", "Airtable base", ("baseId", "base_id"),
                       "The base ID (app...), from the base's URL."),
        ),
        open_tools=frozenset({"ping"}),
        note="When you sign in, Airtable asks which bases to share: pick only this client's.",
    ),
    Remote(
        "sentry", "Sentry", "https://mcp.sentry.dev/mcp", signin=True,
        paste="Sentry user auth token",
        paste_url="https://sentry.io/settings/account/api/auth-tokens/",
        paste_steps="Create a user auth token with read access to the projects you want.",
        auth_scheme="Sentry-Bearer",
        scope=(
            ScopeField("organization", "Sentry organization", ("organizationSlug",),
                       "The organization's slug, from its URL."),
            ScopeField("project", "Project", ("projectSlug",),
                       "Optional: a project slug. Leave empty for the whole organization."),
        ),
        open_tools=frozenset({"search_docs", "get_doc", "whoami"}),
    ),
    Remote(
        "notion", "Notion", "https://mcp.notion.com/mcp", signin=True,
        paste="Notion MCP token (Business and Enterprise plans)",
        paste_url="https://developers.notion.com/guides/mcp/get-started-with-mcp",
        paste_steps="A workspace owner creates an MCP token.",
        note="Notion can't be narrowed to pages: a link reaches what your account can see.",
    ),
    Remote(
        "linear", "Linear", "https://mcp.linear.app/mcp", signin=True,
        paste="Linear API key", paste_url="https://linear.app/settings/account/security",
        paste_steps="Create a personal API key.",
        note="Linear can't be narrowed below the workspace you sign in to.",
    ),
    Remote(
        "cloudflare", "Cloudflare", "https://mcp.cloudflare.com/mcp", signin=True,
        paste="Cloudflare API token", paste_url="https://dash.cloudflare.com/profile/api-tokens",
        paste_steps="Create an account API token with only the permissions you want.",
        scope=(
            ScopeField("account", "Cloudflare account", ("account_id", "accountId"),
                       "The account ID (the dashboard's right-hand column)."),
        ),
        open_tools=frozenset({"docs", "search", "whoami"}),
        note="You choose permissions when you sign in; an empty answer may mean a missing one.",
    ),
    Remote(
        "atlassian", "Atlassian (Jira, Confluence)", "https://mcp.atlassian.com/v2/mcp",
        signin=True, paste=None,
        note="You pick the site when you sign in. Calls use Atlassian's Rovo credits.",
    ),
    Remote(
        "retell", "Retell AI", "https://mcp.retellai.com", signin=False,
        paste="Retell API key", paste_url="https://dashboard.retellai.com/",
        paste_steps="In the Retell dashboard, open Settings, then API Keys, and create a key "
        "(one per client's workspace).",
        read_tools=frozenset({"list_api_endpoints", "get_api_endpoint_schema"}),
        endpoint_tools={"invoke_api_endpoint": ("endpoint_name", "endpoint")},
        note="Reading agents, calls and numbers runs. Creating or updating agents, placing "
        "calls and buying numbers wait for your approval; deleting needs the name typed.",
    ),
    Remote(
        "github", "GitHub", "https://api.githubcopilot.com/mcp/", signin=False,
        paste="GitHub fine-grained token",
        paste_url="https://github.com/settings/personal-access-tokens/new",
        paste_steps="Create a fine-grained token limited to this client's repositories.",
        scope=(
            ScopeField("owner", "Owner", ("owner",), "The user or organization, like acme-inc."),
            ScopeField("repo", "Repository", ("repo",), "Optional: one repository's name."),
        ),
        open_tools=frozenset({"get_me", "search_code"}),
        note="GitHub doesn't let apps register themselves, so it takes a token.",
        shelf=False,
    ),
)  # fmt: skip

BY_KEY = {remote.key: remote for remote in CATALOG}


class ScopeError(ValueError):
    """A call outside the link's scope. The message is for the agent."""


@dataclass(frozen=True)
class ScopeSpec:
    """How links to one kind of connection can be narrowed."""

    name: str
    fields: tuple[ScopeField, ...]
    open_tools: frozenset[str] = frozenset()


# Ringkeeper's own adapters that can be narrowed, by connection type. A Vercel
# project pins only the tools that name one; the team pins every call.
NATIVE: dict[str, ScopeSpec] = {
    "airtable": ScopeSpec(
        "Airtable",
        (ScopeField("base", "Airtable base", ("base_id",), "The base ID (app...)."),),
        frozenset({"whoami"}),
    ),
    # A project's link pins one repository; the account-wide tools (list_repos) aren't
    # available on it.
    "github": ScopeSpec(
        "GitHub",
        (ScopeField("repo", "Repository", ("repo",), "owner/name, like acme-inc/website."),),
    ),
    "vercel": ScopeSpec(
        "Vercel",
        (
            ScopeField("team", "Vercel team", ("team_id",), "The team ID (team_...)."),
            ScopeField("project", "Project", ("project",), "Optional: a project's name or ID."),
        ),
        frozenset({"whoami"}),
    ),
}


def spec_for(service: str, config: Mapping[str, Any] | None) -> ScopeSpec | None:
    """The scope rules for a connection: its remote service's, or its adapter's."""
    if service == "mcp":
        remote = BY_KEY.get(str((config or {}).get("remote") or ""))
        if remote is None or not remote.scope:
            return None
        return ScopeSpec(remote.name, remote.scope, remote.open_tools)
    return NATIVE.get(service)


def check_scope(spec: ScopeSpec | None, scope: Mapping[str, Any]) -> dict[str, str]:
    """The scope a link may store: known fields, non-empty strings, and a later
    field (a branch) only with the one before it (its project)."""
    fields = spec.fields if spec is not None else ()
    cleaned = {k: str(v).strip() for k, v in scope.items() if v is not None and str(v).strip()}
    if unknown := sorted(set(cleaned) - {f.key for f in fields}):
        what = spec.name if spec is not None else "This connection"
        raise ScopeError(f"{what} can't be narrowed by {', '.join(unknown)}")
    for before, f in itertools.pairwise(fields):
        if f.key in cleaned and before.key not in cleaned:
            raise ScopeError(f"Set the {before.label} before the {f.label}.")
    return cleaned


def narrow(
    spec: ScopeSpec,
    scope: Mapping[str, str],
    tool: str,
    arguments: Mapping[str, Any],
    takes: list[str] | None,
) -> dict[str, Any]:
    """The arguments a call runs with under `scope`, or ScopeError.

    `takes` is the tool's argument names (from the server's last tool list for
    an MCP server; None if it hasn't been listed yet: then a missing ID can't
    be filled in, and the call is refused rather than guessed at).
    """
    result = dict(arguments)
    if not scope or tool in spec.open_tools:
        return result
    names = set(takes or [])
    for index, f in enumerate(spec.fields):
        wanted = scope.get(f.key)
        if wanted is None:
            continue
        given = [a for a in f.args if result.get(a) is not None]
        for arg in given:
            if str(result[arg]) != wanted:
                raise ScopeError(
                    f"this project's link to {spec.name} is limited to {f.label.lower()} "
                    f"'{wanted}'; '{tool}' was given {arg}='{result[arg]}'"
                )
        if given:
            continue
        fillable = [a for a in f.args if a in names or a in result]
        if fillable:
            result[fillable[0]] = wanted
        elif index == 0 and takes is None:
            raise ScopeError(
                f"call list_tools first, so Ringkeeper knows which of '{tool}''s arguments "
                f"takes the {f.label.lower()}"
            )
        elif index == 0:
            raise ScopeError(
                f"'{tool}' works across the whole {spec.name} account, and this project's "
                f"link is limited to {f.label.lower()} '{wanted}', so it isn't available here"
            )
    return result


def usable(spec: ScopeSpec, scope: Mapping[str, str], tool: str, takes: list[str]) -> bool:
    """Whether a listed tool can be called through a link with this scope."""
    first = spec.fields[0] if spec.fields else None
    if first is None or first.key not in scope or tool in spec.open_tools:
        return True
    return any(a in takes for a in first.args)
