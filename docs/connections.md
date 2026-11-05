# Connections and shared tools

A **connection** is one client's service: their database, Airtable base, n8n,
Vercel, GitHub, Google account, Twilio number, or an MCP server. You add it
once, link it to that client's projects, and every task in those projects can
use it. A connection can't be linked to another client's project.

A **shared tool** is yours, not a client's: a web browser, files on this
computer, a search tool. It holds no client data, so it can go to any project,
or to all of them (new ones too), with exceptions.

## Adding one

**Connectors › Browse connectors** (or a client's Connections tab, or press Ctrl+K
and type "add a connection"). It asks first whether it's a client's account or your
own tool. Built-in services come first, each a tile that opens its form; then the
services you sign in to; then **Other connectors**, a search of every MCP server,
with the service's own first and community servers folded away. The form links to
the page where the service gives you its key. Tiles are grouped by what they're for: CRM,
Accounting, Payments, CMS and so on. Some vendors sign in through an app of your own: see
[Developer apps](services/developer-apps.md).

Using these in Claude? Claude's connectors stay in your Claude account; connect them
here once per client.

- **Is this live client data?** Asked every time, with no default. Production is
  live data: every change on it waits for your approval. On staging and test data,
  a built-in service's changes run straight away, and are logged (texts and emails
  still wait).
- **Its name** reads like "n8n · n8n.geninfos.com". Change it under Details › Change.
- **Link to project…** on its card links it to any of the client's projects, and
  each project's Connections tab has **Link a connection**.
- **Test connection** makes one read-only call and tells you what it found.
- **Narrow it** (on the client's page): limit a link to one base, repository,
  project or folder, so a project sees only that.

## Link a connection to a project

A connection does nothing until a project uses it. On the client's Connections tab,
each card says where it's used and has **Link to project…**: tick one or several
projects, and narrow each if you like. On a project's Connections tab, **Link a
connection** lists the client's connections that aren't linked yet and your own tools.
A client with no projects says so, with **Add project** and **From Claude**.

## Found on this computer

Ringkeeper prefers logins you already have to keys you'd paste. Connectors lists what
you're signed in to here: GitHub CLI and Vercel CLI, and AWS profiles and SSO, Azure CLI,
Google Cloud CLI and OCI. Each is offered once: **Connect** makes it your own tool, and you
choose which projects use it; **Skip** stops offering it. Only who it signs in as is shown.
The cloud logins are listed for now and can't connect yet.

A project's page lists what its folder holds, the same way: keys in its `.env` and config
files, and MCP servers in `.mcp.json`, `.cursor/mcp.json`, `.vscode/mcp.json`,
`.codex/config.toml`, Claude Code's settings for that folder, and its own code. Nothing is
added until you connect one. A server on a temporary tunnel like `trycloudflare.com`
connects with a warning: it stops working when the tunnel does. A key for a service
Ringkeeper has no connection for yet is named and left in the file.

## All of a client's projects

A connection can go to all of its client's projects, and to projects added later. When
you add one and the client has more than one project, that's what the success card
suggests; untick any project to leave it out, or choose **Choose projects instead**. To
change it later, open the connection's card: Details › All … projects. The card says what
it reaches, like "All Acme projects except Website".

Each project's link can still be narrowed under Details, to one base, branch or site. A
client's connection never reaches another client's projects.

## Your own tools (shared tools)

A shared tool is yours and holds no client's data: a browser like Playwright, files on
this computer, public documentation. **Connectors › Add your own tool**, search for it,
then choose **For all projects** or link it to some. To leave a client or project out,
open its card: Details › All projects › Except….

## Where the key goes

Into Ringkeeper's vault, encrypted with a key kept in Windows' credential store
(or your server's key file). Keys never go to the AI, never appear in logs, and
aren't shown again after you save them. To change one, paste the new key.

Every service has its own page: [services](services/README.md) (where to get
the token, what's held for approval, how to narrow it, what can be undone).
