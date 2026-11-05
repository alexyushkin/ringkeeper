# Clerk

Read and manage an app's users, sessions and organizations through Clerk's Backend API.

One connection is one instance of one app. Each instance has its own secret key, so a
client's development and production instances are two connections, marked as test data and
production. The key says which it is: `sk_test_` for development and `sk_live_` for
production. Ringkeeper won't let a live key be labelled as test data.

## Add it

In the [Clerk Dashboard](https://dashboard.clerk.com/last-active?path=api-keys), pick the
application and the instance, open **API keys**, and copy the secret key. Then use the
**Clerk** tile under Connectors. If `CLERK_SECRET_KEY` is in a project's `.env` file,
Ringkeeper offers it on the project's page.

The key reaches that one instance and nothing else, so linking the connection to a project
is what scopes it.

## What's held

- **Runs:** reading users, sessions, organizations, invitations and the instance's settings.
- **Waits for approval:** updating metadata, and sending or taking back invitations.
- **Needs the name typed, in the app:** deleting, banning or locking users, ending sessions,
  changing organization roles or members, deleting organizations, and anything that changes
  sign-in settings, including allowlists and blocklists.

Metadata that sets a role, plan or permission also needs the name typed, because an app
often uses it to decide what someone may do. So does an organization invitation with any
role other than `org:member`.

Metadata edits can be undone for 7 days. Nothing else here can be undone from Ringkeeper.

## Good to know

- Ringkeeper uses Clerk's Backend API, not its hosted MCP server: that server signs in as a
  Dashboard user and has no tools for metadata, invitations, bans, sessions or organizations.
- A few things are left out on purpose: setting a password, signing in as a user, and
  anything that answers with a secret.
