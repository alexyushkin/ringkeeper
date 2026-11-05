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
from typing import Any, cast
from urllib.parse import unquote

from ringkeeper import cloud_rules


@dataclass(frozen=True)
class ScopeField:
    key: str  # what the link stores: {"project": "..."}
    label: str
    args: tuple[str, ...]  # the argument names that carry it
    hint: str = ""
    # A folder or path: a call must stay under it, rather than name exactly it. It stands
    # on its own, without the field before it.
    prefix: bool = False
    # A cloud's CLI flag that carries it, in a command tool (`Remote.command_tools`).
    flags: tuple[str, ...] = ()
    every_command: bool = True  # every command takes the flag, so it can be added to any


@dataclass(frozen=True)
class Guard:
    """What a kind of service needs, whatever a tool's own rule says: a tool with one of
    `final` in its name needs the connection's name typed (unless it also has one of
    `unless`), and any tool that doesn't read by its name waits for approval. The per-tool
    setting can make these stricter, never looser."""

    final: frozenset[str] = frozenset()
    unless: frozenset[str] = frozenset()
    why: str = "needs the name typed on this kind of service"
    never_trusted: bool = False  # nothing here runs by itself, even after an approval


GUARDS: dict[str, Guard] = {
    "crm": Guard(
        frozenset(
            {
                "delete",
                "remove",
                "merge",
                "bulk",
                "batch",
                "import",
                "send",
                "email",
                "sms",
                "message",
                "enroll",
                "enrol",
                "sequence",
                "campaign",
                "broadcast",
            }
        )
    ),
    "accounting": Guard(
        frozenset(
            {
                "post",
                "send",
                "void",
                "pay",
                "payment",
                "payments",
                "journal",
                "bank",
                "payroll",
                "refund",
                "delete",
                "remove",
                "approve",
                "reconcile",
                "writeoff",
                "transfer",
            }
        ),
        never_trusted=True,
    ),
    "cms": Guard(
        frozenset(
            {
                "publish",
                "unpublish",
                "delete",
                "remove",
                "theme",
                "plugin",
                "settings",
                "setting",
                "domain",
                "deploy",
                "user",
                "users",
                "role",
                "roles",
                "webhook",
            }
        )
    ),
    "payments": Guard(
        frozenset(
            {
                "charge",
                "refund",
                "capture",
                "void",
                "payout",
                "transfer",
                "subscription",
                "dispute",
                "pay",
                "payment",
                "cancel",
                "delete",
                "remove",
                "send",
                "finalize",
                "mandate",
            }
        ),
        unless=frozenset({"link"}),
    ),
    "ads": Guard(frozenset({"budget", "delete", "remove", "publish", "activate", "launch"})),
    "cloud": Guard(
        frozenset(
            {
                "delete",
                "remove",
                "create",
                "deploy",
                "assign",
                "role",
                "policy",
                "secret",
                "secrets",
                "start",
                "restart",
                "scale",
                "purchase",
                "key",
                "keys",
                "password",
                "token",
            }
        )
    ),
    "held": Guard(),  # every change waits for approval; nothing more
}


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
    category: str = ""  # as the catalog groups it (empty: its old place)
    guard: str = ""  # a key of GUARDS: rules the per-tool setting can't lower
    # Tools that do anything at all behind one name: the name can't say what a call does,
    # so each call needs the connection's name typed.
    opaque_tools: frozenset[str] = frozenset()
    url_label: str = ""  # each customer has their own address: what to ask for
    url_hint: str = ""
    basic: bool = False  # the pasted "user:password" goes as HTTP Basic
    minor_units: bool = False  # its amounts are in cents, for the approval card
    # A server that runs on this computer with your CLI's login: how to start it.
    local: tuple[str, ...] = ()
    local_env: Mapping[str, str] = field(default_factory=dict[str, str])
    # Tools that run any command of a CLI, and the argument that holds it (ringkeeper.cloud_rules).
    cli: str = ""
    command_tools: Mapping[str, str] = field(default_factory=dict[str, str])
    # One login reaches many clients' accounts: a project's link has to say which.
    scope_required: bool = False
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
    # --- Cloud: on this computer, with your own login ---
    Remote(
        "aws", "AWS", "", signin=False, paste=None, category="Cloud", guard="cloud", cli="aws",
        local=("uvx", "--from", "awslabs.aws-api-mcp-server==1.5.6",
               "awslabs.aws-api-mcp-server"),
        local_env={"AWS_API_MCP_TELEMETRY": "false"},
        command_tools={"call_aws": "cli_command"},
        read_tools=frozenset({"suggest_aws_commands"}),
        scope=(
            ScopeField("region", "Region", ("region",), "Optional: like us-east-1.",
                       flags=("--region",)),
        ),
        open_tools=frozenset({"suggest_aws_commands"}),
        note="One connection is one AWS profile on this computer, so one account. It runs "
        "AWS's own server with your login.",
    ),
    Remote(
        "azure", "Azure", "", signin=False, paste=None, category="Cloud", guard="cloud",
        local=("npx", "-y", "@azure/mcp@3.0.0", "server", "start", "--mode", "all"),
        scope=(
            ScopeField("subscription", "Azure subscription", ("subscription",),
                       "The client's subscription ID."),
            ScopeField("resource_group", "Resource group",
                       ("resource-group", "resource_group", "resourceGroup"),
                       "Optional: one resource group."),
        ),
        scope_required=True,
        note="Your own Azure CLI login, so each project's link names the client's subscription. "
        "It runs Microsoft's own server on this computer.",
    ),
    Remote(
        "gcloud", "Google Cloud", "", signin=False, paste=None, category="Cloud", guard="cloud",
        cli="gcloud", local=("npx", "-y", "@google-cloud/gcloud-mcp@0.5.3"),
        command_tools={"run_gcloud_command": "args"},
        scope=(
            ScopeField("project", "Google Cloud project", ("project",), "The client's project ID.",
                       flags=("--project",)),
        ),
        scope_required=True,
        note="Your own gcloud login, so each project's link names the client's project. It runs "
        "Google's own server on this computer.",
    ),
    Remote(
        "oci", "Oracle Cloud (OCI)", "", signin=False, paste=None, category="Cloud",
        guard="cloud", cli="oci",
        local=("uvx", "--from", "oracle.oci-api-mcp-server==2.1.5", "oracle.oci-api-mcp-server"),
        command_tools={"run_oci_command": "command"},
        read_tools=frozenset({"get_oci_command_help"}),
        scope=(
            ScopeField("compartment", "OCI compartment", ("compartment",),
                       "The client's compartment OCID.", flags=("--compartment-id", "-c"),
                       every_command=False),
        ),
        open_tools=frozenset({"get_oci_command_help"}),
        scope_required=True,
        note="Your own OCI CLI login, so each project's link names the client's compartment. "
        "A command on a project's link has to name that compartment. Oracle marks this server "
        "as not meant for production use.",
    ),
    # --- CRM ---
    Remote(
        "gohighlevel", "GoHighLevel", "https://services.leadconnectorhq.com/mcp/anthropic/v2",
        signin=True, paste=None, category="CRM", guard="crm",
        read_tools=frozenset({"list_locations", "search_operations", "describe_operation"}),
        opaque_tools=frozenset({"execute_operation"}),
        note="You pick the sub-account when you sign in. Every action goes through one tool, so "
        "each one needs the name typed.",
    ),
    Remote(
        "monday", "monday.com", "https://mcp.monday.com/mcp", signin=True, paste=None,
        category="CRM", guard="crm", opaque_tools=frozenset({"all_monday_api"}),
        note="monday's server is in beta. Its open GraphQL tool needs the name typed.",
    ),
    Remote(
        "attio", "Attio", "https://mcp.attio.com/mcp", signin=True, paste=None, category="CRM",
        guard="crm", note="A link reaches what your Attio user can see.",
    ),
    Remote(
        "close", "Close", "https://mcp.close.com/mcp", signin=True, paste=None, category="CRM",
        guard="crm", note="Choose what it may do when you sign in.",
    ),
    Remote(
        "zoho_crm", "Zoho CRM", "", signin=False, paste=None, category="CRM", guard="crm",
        url_label="Your Zoho MCP address", url_hint="From the Zoho MCP console: "
        "https://<name>-<org>.zohomcp.com/mcp/<key>/message. It holds a key, so it's kept in "
        "the vault.",
    ),
    # --- Accounting ---
    Remote(
        "freshbooks", "FreshBooks", "https://mcp.freshbooks.com/v1", signin=True, paste=None,
        category="Accounting", guard="accounting",
        note="FreshBooks' server only reads for now. A sign-in reaches every business on the "
        "login.",
    ),
    Remote(
        "zoho_books", "Zoho Books", "https://claude-zohobooks.zohomcp.com/mcp/message",
        signin=True, paste=None, category="Accounting", guard="accounting",
        note="This is Zoho's US address. No accounting change runs by itself, even on test "
        "data.",
    ),
    Remote(
        "odoo", "Odoo", "", signin=True, paste=None, category="Accounting", guard="accounting",
        url_label="Your Odoo address", url_hint="Your database address plus /mcp, like "
        "https://acme.odoo.com/mcp. It needs Odoo 20 or saas-19.4, with Allow Dynamic Client "
        "Registration ticked in Odoo's MCP settings.",
    ),
    # --- CMS ---
    Remote(
        "wordpress_com", "WordPress.com", "https://public-api.wordpress.com/wpcom/v2/mcp/v1",
        signin=True, paste=None, category="CMS", guard="cms",
        note="One sign-in reaches every site on the account. Choose Read only at sign-in if "
        "that's all you need.",
    ),
    Remote(
        "wordpress", "WordPress (your own site)", "", signin=False,
        paste="Username and application password, as user:password", category="CMS", guard="cms",
        paste_url="https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/",
        paste_steps="Install the WordPress MCP Adapter plugin (WordPress 6.9 or later). In Users, "
        "open your profile and add an application password.",
        url_label="Your site's MCP address",
        url_hint="https://example.com/wp-json/mcp/mcp-adapter-default-server",
        basic=True,
    ),
    Remote(
        "webflow", "Webflow", "https://mcp.webflow.com/mcp", signin=True, paste=None,
        category="CMS", guard="cms", note="You pick the sites when you sign in.",
    ),
    Remote(
        "wix", "Wix", "https://mcp.wix.com/mcp", signin=True, paste=None, category="CMS",
        guard="cms", opaque_tools=frozenset({"CallWixSiteAPI", "ExecuteWixAPI"}),
        note="Wix's open API tools need the name typed.",
    ),
    Remote(
        "sanity", "Sanity", "https://mcp.sanity.io", signin=True, paste=None, category="CMS",
        guard="cms", note="Editing drafts waits for approval. Publishing needs the name typed.",
    ),
    Remote(
        "contentful", "Contentful", "https://mcp.contentful.com/mcp", signin=True, paste=None,
        category="CMS", guard="cms",
        note="Install the Contentful MCP app in the space first. For a space in Europe, add "
        "https://mcp.eu.contentful.com/mcp by its address.",
    ),
    Remote(
        "strapi", "Strapi", "", signin=False, paste="Strapi Admin API token", category="CMS",
        guard="cms", paste_steps="In Strapi's admin, open Settings, then API Tokens. Strapi's "
        "MCP server needs turning on (mcp.enabled, Strapi 5.47 or later).",
        url_label="Your Strapi MCP address", url_hint="https://cms.example.com/mcp",
    ),
    Remote(
        "payload", "Payload", "", signin=False, paste="Payload user API key", category="CMS",
        guard="cms", auth_scheme="users API-Key",
        paste_steps="Add @payloadcms/plugin-mcp to your Payload app, and turn on an API key for "
        "a user. This sends it as users API-Key: a different auth collection needs its own "
        "name, so add that server by its address instead.",
        url_label="Your Payload MCP address", url_hint="https://cms.example.com/api/mcp",
    ),
    # --- Payments ---
    Remote(
        "stripe", "Stripe", "https://mcp.stripe.com", signin=True, paste=None,
        category="Payments", guard="payments", minor_units=True,
        note="You pick the account and what it may do when you sign in. Use a sandbox while "
        "you try it.",
    ),
    Remote(
        "paypal", "PayPal", "https://mcp.paypal.com/mcp", signin=True, paste=None,
        category="Payments", guard="payments",
    ),
    Remote(
        "mollie", "Mollie", "https://mcp.mollie.com/mcp", signin=True, paste=None,
        category="Payments", guard="payments", note="Mollie's server launched in October 2026.",
    ),
    Remote(
        "paddle", "Paddle", "https://mcp.paddle.com/mcp", signin=True, paste=None,
        category="Payments", guard="payments", minor_units=True,
        note="Paddle starts read-only: an admin raises it under Connectors, then MCP.",
    ),
    Remote(
        "gocardless", "GoCardless", "https://mcp.gocardless.com", signin=True, paste=None,
        category="Payments", guard="payments", minor_units=True,
        note="You pick Sandbox or Live, and what it may do, when you sign in.",
    ),
    # --- Other services ---
    Remote(
        "apollo", "Apollo", "https://mcp.apollo.io/mcp", signin=True, paste=None,
        category="CRM", guard="held", note="Tools that spend credits wait for your approval.",
    ),
    Remote(
        "apify", "Apify", "https://mcp.apify.com", signin=True, paste=None, category="AI",
        guard="held", note="Running an Actor costs credits, so it waits for your approval.",
    ),
    Remote(
        "netlify", "Netlify", "https://mcp.netlify.com/mcp", signin=True, paste=None,
        category="Dev tools", guard="held",
    ),
    Remote(
        "posthog", "PostHog", "https://mcp.posthog.com/mcp", signin=True, paste=None,
        category="Dev tools", guard="held",
    ),
    Remote(
        "tavily", "Tavily", "https://mcp.tavily.com/mcp/", signin=True, paste=None, category="AI",
        note="Each search uses Tavily credits.",
    ),
    Remote(
        "pipeboard", "Meta Ads (Pipeboard)", "https://mcp.pipeboard.co/meta-ads-mcp",
        signin=True, paste=None, category="Other", guard="ads",
        note="Pipeboard is a third party, not Meta. Budgets and launching ads need the name "
        "typed.",
    ),
    Remote(
        "upwork", "Upwork", "https://mcp.upwork.com/mcp", signin=True, paste=None,
        category="Other", guard="held", note="Proposals and messages wait for your approval.",
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
    cli: str = ""
    command_tools: Mapping[str, str] = field(default_factory=dict[str, str])
    required: bool = False


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
    # A folder or API path: the call has to stay under it.
    "http": ScopeSpec(
        "this API",
        (
            ScopeField(
                "path", "Path", ("path",), "Only under this path, like /v1/forms.", prefix=True
            ),
        ),
        frozenset({"list_endpoints"}),
    ),
    "s3": ScopeSpec(
        "this storage",
        (
            ScopeField("bucket", "Bucket", ("bucket",), "One bucket's name."),
            ScopeField(
                "folder",
                "Folder",
                ("key", "prefix"),
                "Optional: only under this folder, like clients/acme/.",
                prefix=True,
            ),
        ),
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


# Business systems that can be narrowed, by vendor (adapters.vendor fills this in).
VENDOR_SCOPES: dict[str, ScopeSpec] = {}


def spec_for(service: str, config: Mapping[str, Any] | None) -> ScopeSpec | None:
    """The scope rules for a connection: its remote service's, or its adapter's."""
    if service == "vendor":
        return VENDOR_SCOPES.get(str((config or {}).get("vendor") or ""))
    if service == "mcp":
        remote = BY_KEY.get(str((config or {}).get("remote") or ""))
        if remote is None or not remote.scope:
            return None
        return ScopeSpec(
            remote.name, remote.scope, remote.open_tools, remote.cli, remote.command_tools,
            remote.scope_required,
        )  # fmt: skip
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
        if f.key in cleaned and before.key not in cleaned and not f.prefix:
            raise ScopeError(f"Set the {before.label} before the {f.label}.")
    return cleaned


def under(wanted: str, value: str) -> bool:
    """Whether a path stays under a folder: /v1/forms covers /v1/forms/3, not /v1/forms-old
    or /v1/forms/../users. Encoded dots count as dots."""
    decoded = unquote(unquote(value)).split("?", 1)[0]
    if ".." in decoded.replace("\\", "/").split("/"):
        return False
    base = wanted.rstrip("/")
    return decoded == base or decoded.startswith(base + "/")


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
    if tool in spec.command_tools:
        return _narrow_command(spec, scope, tool, result)
    names = set(takes or [])
    for index, f in enumerate(spec.fields):
        wanted = scope.get(f.key)
        if wanted is None:
            continue
        given = [a for a in f.args if result.get(a) is not None]
        for arg in given:
            fits = under(wanted, str(result[arg])) if f.prefix else str(result[arg]) == wanted
            if not fits:
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


def _narrow_command(
    spec: ScopeSpec, scope: Mapping[str, str], tool: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    """A command stays inside the link: its flag names the link's value, or gets it."""
    arg = spec.command_tools[tool]
    command: Any = arguments.get(arg)
    words = cloud_rules.tokens(spec.cli, command)
    if words is None:
        raise ScopeError(f"this project's link can't check that {spec.name} command")
    for f in spec.fields:
        wanted = scope.get(f.key)
        if wanted is None or not f.flags:
            continue
        given = cloud_rules.flag_value(words, f.flags)
        if given is not None and given != wanted:
            raise ScopeError(
                f"this project's link to {spec.name} is limited to {f.label.lower()} '{wanted}'; "
                f"that command names '{given}'"
            )
        if given is not None:
            continue
        verbs = cloud_rules.positional(words)
        if not f.every_command and verbs[-1:] != ["list"]:
            raise ScopeError(
                f"this project's link to {spec.name} is limited to {f.label.lower()} '{wanted}', "
                f"and that command doesn't name it, so it can't be checked: add {f.flags[0]}"
            )
        added = [f.flags[0], wanted]
        command = (
            [*cast(list[Any], command), *added]
            if isinstance(command, list)
            else (f"{command} {' '.join(added)}")
        )
        words = [*words, *added]
    return {**arguments, arg: command}


def usable(spec: ScopeSpec, scope: Mapping[str, str], tool: str, takes: list[str]) -> bool:
    """Whether a listed tool can be called through a link with this scope."""
    first = spec.fields[0] if spec.fields else None
    if first is None or first.key not in scope or tool in spec.open_tools:
        return True
    if tool in spec.command_tools:
        return True
    return any(a in takes for a in first.args)
