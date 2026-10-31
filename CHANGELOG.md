# Changelog

Each release of the desktop app has an entry here; the release workflow
publishes it as the release notes and the app shows it with the update.

## Unreleased

- **Shared tools are easier to add.** Add a connection asks first: "A client's
  account or your own tool?", and only a client's account asks whose it is.
  Connections has **Add a shared tool**, which opens the catalog for shared
  tools only.
- **Remote servers can be shared tools** too. The catalog suggests a client's
  account when a server wants a sign-in or a key, and your own tool when it
  answers with nothing; a server that needs nothing connects without a key.
- **Voice has a Language choice.** Settings › Voice: Auto-detect, or a
  searchable list of the speech model's languages. With the Fast model and a
  language other than English, it suggests Accuracy, which handles other
  languages much better.

## 0.1.0-beta.4 (2026-10-30)

- **"Use my Claude Code login" says what it found.** An old login says
  "Found an old Claude Code login from <date> that has expired" and points to
  `claude setup-token`; no login says so, and that the Claude desktop app keeps
  its own. The `setup-token` token comes first in setup and Settings: it lasts a
  year.
- **Shared tools.** A browser, files on this computer, a fetcher: MCP servers
  that hold no client's data can now be your own tool, linked to any client's
  projects or to all of them (new projects get them too). A client's
  connections are still only for that client's projects. Their writes still
  wait for your approval.
- **Importing from Claude Desktop** asks, for each server, whether it's a
  client's account or your own tool (it suggests one), and gives it to several
  projects at once, or to all projects for a shared tool. Skip is still there.
  The catalog's local servers and extension files ask the same.
- **After adding a connection** (a client's page, a project's page, Add a
  connection), a card offers **Also use in…** for the other projects that may
  use it (narrowed per project where the service allows) and **Add another
  connection**.
- **First run:** step 2 has **Browse the catalog** next to the Claude Desktop
  import, and the connection step takes as many connections as you like before
  Continue, listing the ones added.
- **Shorter pages.** Project and client pages have tabs (Overview, Connections,
  Tasks, Settings), and Settings has tabs too (Claude, Models, Voice,
  Notifications, Advanced, About). Sections fold away and show a one-line
  summary while folded; what's open is remembered per page. Connections, catalog
  results and the Claude Desktop import list are compact rows that open for
  details.
- **Installing a newer version over an older one** no longer asks "uninstall
  before installing / do not uninstall": it upgrades in place, keeps your data,
  and goes from progress straight to Finish. Reinstalling the same version, or
  installing an older one, still asks.
- Changing a connection's kind of data no longer drops an MCP server's saved
  settings (its tool rules and package).

## 0.1.0-beta.3 (2026-10-28)

- **First run can use your Claude subscription** when personal subscription
  mode is on (`RINGKEEPER_ALLOW_SUBSCRIPTION=1`): step 1 offers it next to the
  API key, with a token from `claude setup-token` or, in the desktop app, your
  Claude Code login. Without it, step 1 asks for an API key only.
- **Server installs follow the same rule in Settings:** the subscription is
  offered only with `RINGKEEPER_ALLOW_SUBSCRIPTION=1`. An install already on
  its subscription keeps running and shows a one-line notice to set it.

## 0.1.0-beta.2 (2026-10-27)

- **Install extension.** Claude Desktop extension files (`.mcpb`) install from
  the catalog: open the file, check what it says it is (its publisher, tools,
  the command it runs, the settings it needs), confirm once, and it's unpacked
  into Ringkeeper's data folder and connected. It follows the same approval
  rules as an extension imported from Claude Desktop.
- **First run says what Ringkeeper is for:** one person running AI agents
  across many clients without switching connections.

## 0.1.0-beta.1 (2026-10-26)

The first beta of the desktop app for Windows.

- **One installer, nothing else to install.** Ringkeeper installs for your
  user only (no administrator rights) and brings what it needs with it:
  Python, git for its workers, and the Claude Code engine its agents run on.
  Your data stays in `%LOCALAPPDATA%\Ringkeeper`.
- **Projects bound to their own services.** Each client's projects reach only
  that client's databases, Airtable bases, Vercel projects, n8n and other
  connections, connected in one click where the service allows it.
- **An open catalog of MCP servers.** Search every server in the official MCP
  Registry (it works offline from what you've seen), connect remote ones with
  one-click sign-in or a key, install local ones (npm, PyPI, containers)
  pinned to the version shown after one confirmation, or add any server by its
  address. Services with built-in rules stay on a Recommended shelf on top.
  Servers without them get generic rules: read-only tools run, the rest wait
  for you.
- **Approvals.** Writes to production, messages and other risky steps wait for
  you, in the app or in Telegram.
- **Plans first for features**, models and effort per role, and a spending cap
  per task that asks before going further.
- **Voice requests**, transcribed on your computer. The speech engine and model
  download the first time you use voice (about 160 MB, the size shown first),
  so the installer stays small for everyone who doesn't. For better accuracy
  with accents, names and other languages, choose Accuracy in Settings › Voice
  (a bigger download).
- **Updates** are downloaded in the background and offered with "Update ready,
  restart when you're done". Nothing restarts on its own, and updates never
  touch your data.
