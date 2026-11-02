# Connections and shared tools

A **connection** is one client's service: their database, Airtable base, n8n,
Vercel, GitHub, Google account, Twilio number, or an MCP server. You add it
once, link it to that client's projects, and every task in those projects can
use it. A connection can't be linked to another client's project.

A **shared tool** is yours, not a client's: a web browser, files on this
computer, a search tool. It holds no client data, so it can go to any project,
or to all of them (new ones too), with exceptions.

## Adding one

**Connections › Add** (or press Ctrl+K and type "add a connection"). Pick the
service, then either sign in (where the service allows it) or paste a key. The
form links to the page where the service gives you that key.

- **Production, staging or development.** Production is for live client data:
  every change on it waits for your approval. Staging and development run
  changes straight away, and log them.
- **Test connection** makes one read-only call and tells you what it found.
- **Narrow it** (on the client's page): limit a link to one base, repository,
  project or folder, so a project sees only that.

## Where the key goes

Into Ringkeeper's vault, encrypted with a key kept in Windows' credential store
(or your server's key file). Keys never go to the AI, never appear in logs, and
aren't shown again after you save them. To change one, paste the new key.

Every service has its own page: [services](services/) (where to get the token,
what's held for approval, how to narrow it).
