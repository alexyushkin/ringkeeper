"""Sign-in services: Clerk, through its Backend API.

Clerk's own server (mcp.clerk.com) signs in as a Dashboard user and works on whole
applications; it has no tools for metadata, invitations, bans, sessions or organizations,
and its older agent toolkit is deprecated. The Backend API takes one secret key per
instance, so a connection is one instance: a client's app, with its development and
production instances as Test and Production. That key can reach nothing else, so the
connection is the scope.

Left out on purpose: PATCH /users/{id} (it can set a password), sign-in tokens and session
tokens (they act as the user), and every GET that answers with a secret (a machine's
secret key, an API key's secret, a user's OAuth access tokens).
"""

import re
from typing import Any, cast

from ringkeeper.adapters.vendor_spec import (
    JSON,
    Auth,
    Secret,
    Snapshot,
    Vendor,
    final,
    held,
    reads,
)
from ringkeeper.models import Env

API_VERSION = "2026-05-12"  # the version @clerk/backend 3.26 sends
SETTINGS = "changes how people sign in"
METADATA = ("public_metadata", "private_metadata", "unsafe_metadata")
# An app's own roles usually live in metadata: setting one changes who may do what.
ACCESS_KEY = re.compile(r"role|permission|admin|plan|tier|entitlement|access", re.I)
MEMBER = "org:member"


def _access_keys(value: Any) -> list[str]:
    """The metadata keys that look like access, however deep."""
    if not isinstance(value, dict):
        return []
    found: list[str] = []
    for key, inner in cast(dict[str, Any], value).items():
        if ACCESS_KEY.search(str(key)):
            found.append(str(key))
        found += _access_keys(inner)
    return found


def _metadata_why(args: JSON) -> str | None:
    body = cast(JSON, args.get("body") or {})
    keys = [k for field in METADATA for k in _access_keys(body.get(field))]
    return (
        f"sets {', '.join(sorted(set(keys)))}, which can change what someone may do"
        if keys
        else None
    )


def _invite_why(args: JSON) -> str | None:
    role = str(cast(JSON, args.get("body") or {}).get("role") or "")
    return None if role == MEMBER else f"invites someone with the role {role or '(none given)'}"


def env_of(fields: JSON) -> Env | None:
    key = str(fields.get("api_key") or "")
    if key.startswith("sk_live_"):
        return Env.PROD
    return Env.DEV if key.startswith("sk_test_") else None


def _who(found: JSON) -> str:
    kind = "production" if found.get("environment_type") == "production" else "development"
    return f"the {kind} instance {found.get('id')}"


USER_BACK = Snapshot("get_user", METADATA, "replace_user_metadata", says="the user's metadata")

CLERK = Vendor(
    "clerk", "Clerk", "Dev tools", "Users, sessions, organizations", "https://api.clerk.com/v1",
    auth=Auth("bearer"),
    secrets=(
        Secret("api_key", "Secret key", "From API keys in the Clerk Dashboard: sk_test_... or "
               "sk_live_...", pattern=r"^sk_(test|live)_[A-Za-z0-9]+$"),
    ),
    headers={"Clerk-API-Version": API_VERSION},
    probe="get_instance", who=_who, env_of=env_of,
    env_vars={"CLERK_SECRET_KEY": "api_key"},
    words=("clerk", "auth", "login", "users", "sign in", "sessions", "organizations"),
    key_url="https://dashboard.clerk.com/last-active?path=api-keys",
    steps="In the Clerk Dashboard, pick the application and the instance (Development or "
    "Production), open API keys, and copy the secret key. Each instance has its own key, so "
    "add one connection per instance.",
    note="One connection is one instance of one app. Reading runs. Metadata and invitations "
    "wait for approval. Deleting or banning users, ending sessions, organization roles and "
    "sign-in settings need the name typed.",
    ops=(
        reads("list_users", "/users", "Find users", "query", "email_address", "organization_id",
              "banned", "limit", "offset", "order_by"),
        reads("count_users", "/users/count", "Count users", "query", "email_address"),
        reads("get_user", "/users/{user_id}", "Read one user"),
        reads("list_user_organizations", "/users/{user_id}/organization_memberships",
              "A user's organizations and roles", "limit", "offset"),
        reads("list_sessions", "/sessions", "Sessions of a user or client", "user_id", "client_id",
              "status", "limit", "offset"),
        reads("get_session", "/sessions/{session_id}", "Read one session"),
        reads("list_organizations", "/organizations", "Find organizations", "query", "user_id",
              "include_members_count", "limit", "offset"),
        reads("get_organization", "/organizations/{organization_id}", "Read one organization"),
        reads("list_organization_members", "/organizations/{organization_id}/memberships",
              "An organization's members and roles", "limit", "offset"),
        reads("list_invitations", "/invitations", "Invitations to the app", "status", "query",
              "limit", "offset"),
        reads("list_organization_invitations", "/organizations/{organization_id}/invitations",
              "Invitations to an organization", "status", "limit", "offset"),
        reads("get_instance", "/instance", "This instance: which one, and its allowed origins"),
        reads("get_organization_settings", "/instance/organization_settings",
              "How organizations are set up here"),
        reads("list_domains", "/domains", "This instance's domains"),
        reads("list_allowlist", "/allowlist_identifiers", "Who may sign up, if that's limited"),
        reads("list_blocklist", "/blocklist_identifiers", "Who may not sign up"),

        # Held. A role in the metadata needs the name, like any change to who may do what.
        final("update_user_metadata", "PATCH", "/users/{user_id}/metadata",
              "Merge into a user's metadata", _metadata_why, *METADATA, only=METADATA,
              undo=USER_BACK),
        final("replace_user_metadata", "PUT", "/users/{user_id}/metadata",
              "Replace a user's metadata", _metadata_why, *METADATA, only=METADATA,
              undo=USER_BACK),
        final("update_organization_metadata", "PATCH", "/organizations/{organization_id}/metadata",
              "Merge into an organization's metadata", _metadata_why, "public_metadata",
              "private_metadata", only=("public_metadata", "private_metadata")),
        held("invite_user", "POST", "/invitations", "Invite someone to the app by email",
             "email_address", "redirect_url", "expires_in_days",
             only=("email_address", "redirect_url", "notify", "ignore_existing",
                   "expires_in_days", "template_slug")),
        held("revoke_invitation", "POST", "/invitations/{invitation_id}/revoke",
             "Take back an invitation"),
        final("invite_to_organization", "POST", "/organizations/{organization_id}/invitations",
              "Invite someone to an organization", _invite_why, "email_address", "role",
              "redirect_url", only=("email_address", "role", "redirect_url", "expires_in_days",
                                    "inviter_user_id", "notify")),
        held("revoke_organization_invitation", "POST",
             "/organizations/{organization_id}/invitations/{invitation_id}/revoke",
             "Take back an organization invitation", only=("requesting_user_id",)),

        # The name typed.
        final("delete_user", "DELETE", "/users/{user_id}", "Delete a user", "deletes a user"),
        final("ban_user", "POST", "/users/{user_id}/ban", "Ban a user", "bans a user"),
        final("unban_user", "POST", "/users/{user_id}/unban", "Lift a user's ban",
              "lets a banned user back in"),
        final("lock_user", "POST", "/users/{user_id}/lock", "Lock a user out", "locks a user out"),
        final("unlock_user", "POST", "/users/{user_id}/unlock", "Unlock a user",
              "lets a locked user back in"),
        final("revoke_session", "POST", "/sessions/{session_id}/revoke", "End a session",
              "signs someone out"),
        final("set_organization_role", "PATCH",
              "/organizations/{organization_id}/memberships/{user_id}",
              "Change a member's role", "changes someone's organization role", "role",
              only=("role",)),
        final("add_organization_member", "POST", "/organizations/{organization_id}/memberships",
              "Add a user to an organization", "gives someone an organization role", "user_id",
              "role", only=("user_id", "role")),
        final("remove_organization_member", "DELETE",
              "/organizations/{organization_id}/memberships/{user_id}",
              "Remove a member", "removes someone from an organization"),
        final("delete_organization", "DELETE", "/organizations/{organization_id}",
              "Delete an organization", "deletes an organization"),
        final("update_instance", "PATCH", "/instance", "Change this instance's settings",
              SETTINGS),
        final("update_restrictions", "PATCH", "/instance/restrictions",
              "Change who may sign up", SETTINGS, "allowlist", "blocklist"),
        final("update_organization_settings", "PATCH", "/instance/organization_settings",
              "Change how organizations work", SETTINGS),
        final("add_to_allowlist", "POST", "/allowlist_identifiers", "Allow someone to sign up",
              SETTINGS, "identifier", "notify", only=("identifier", "notify")),
        final("remove_from_allowlist", "DELETE", "/allowlist_identifiers/{identifier_id}",
              "Take someone off the allowlist", SETTINGS),
        final("add_to_blocklist", "POST", "/blocklist_identifiers", "Block someone from signing up",
              SETTINGS, "identifier", only=("identifier",)),
        final("remove_from_blocklist", "DELETE", "/blocklist_identifiers/{identifier_id}",
              "Take someone off the blocklist", SETTINGS),
    ),
)  # fmt: skip

VENDORS = (CLERK,)
