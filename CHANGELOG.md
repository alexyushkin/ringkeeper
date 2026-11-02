# Changelog

Each release of the desktop app has an entry here; the release workflow
publishes it as the release notes and the app shows it with the update.

## Unreleased

- **Undo you can count on.** An approval says "you can revert this" only after
  checking: the table has a primary key, the Airtable records and the n8n
  workflow can be read. Otherwise it says why it can't be undone, before you
  approve.
- **Mac (Apple Silicon), experimental.** The install guide now covers the Mac
  build: unsigned and not yet tested on a real Mac, so macOS asks you to allow
  it once (System Settings › Privacy & Security › Open Anyway). Reports welcome.

## 0.1.0-beta.11 (2026-11-02)

- **Undo for data changes.** Before an approved or auto-run change to a
  database (a plain UPDATE or DELETE), Airtable records or an n8n workflow,
  Ringkeeper keeps what it's about to change (only that) in the data folder.
  For 7 days, the task page and its conversation show Revert with what it puts
  back; reverting waits for approval like any change, and stops if the data
  changed again meanwhile. Approvals say beforehand when something can't be
  undone (a sent email, a text, a deploy, an INSERT). Kept changes expire after
  7 days; each project has 50 MB for them.
- **Backup and move.** Settings › Backup saves everything (clients, projects,
  connections and their keys, agents, routines, settings, history) into one
  file locked with a password you choose. Restore it on another computer: see
  what comes in first (and which project folders aren't there yet), then
  restore and restart; what was there is kept beside it. Project folders are
  referenced, not copied. Optional weekly backups to a folder you choose,
  keeping the last few.
- **Report a problem.** Help › Report a problem (or Ctrl+K) gathers the
  version, your system, a few counts and the end of the logs, removes keys,
  tokens, passwords and email addresses, and shows you all of it, editable.
  Then open a prefilled GitHub issue, or save it as a zip. Nothing is sent by
  Ringkeeper itself.
- **What's new.** After an update, Home shows what came with it, once.
- **A user guide.** docs/ has a page per idea and per service (where to get
  its key, what waits for approval, what can be undone), installing on Windows
  or a server, backups, security (what's stored where, and what leaves your
  computer), troubleshooting and a FAQ. The README is one screen.

## 0.1.0-beta.10 (2026-11-02)

- **Get started on Home.** A checklist (Claude connected, a first project, a
  first connection, a first task) links to each step and goes away when it's
  done, or when you hide it. Below it, three requests to try, built from your
  own projects and connections, that only read.
- **Demo data.** "Try it with demo data", in the first run and on Home, adds a
  made-up client, Demo Co, with a shop project: sample files in its folder and
  a small shop database, set up as production so a change waits for your
  approval. Try tasks and see a hold before connecting anything real. Remove
  the demo removes all of it, its tasks and conversations too.
- **Ctrl+K.** A command palette from anywhere (or "Search or jump to" in the
  sidebar): switch client or project, start a request, add a connection or a
  shared tool, a new agent or routine, open pending approvals, any page or
  setting. Type a few letters of what you want.
- **Help.** A Help page in the sidebar: the seven ideas (client, project,
  connection, shared tool, agent, routine, hold) in plain words with a picture
  of how they fit, the user guide, and Report a problem. A three-step tour
  after the first run shows the request box, Approvals and Connections; skip
  it, or see it again from Help. A "?" beside Connections, Agents, Routines,
  Settings › Google app and Notifications says what each is for and links to
  the guide.
- **Plain words everywhere.** Errors say what happened and what to do next,
  without internal IDs, command-line flags or developer words (the
  connection's and client's names instead). Telegram, Slack and Discord
  messages lose their short IDs and raw statuses ("✖ acme-web: stopped at its
  spending cap", "Approved by alex (Slack)"). Approvals name the action
  ("Changing Airtable records on acme-crm"). Empty pages say what goes there
  and how to add the first one. Errors that used to vanish (a failed remove,
  a failed toggle) now show.
- **Agents in the sidebar.** An Agents page lists every project's agents,
  narrowed by the client and project switcher, with New agent (it asks which
  project first). The Routines page has New routine: pick the project, then
  its agent, or create one right there if it has none. Empty pages say how
  they fit: an agent is who does the work; a routine is when it runs.

## 0.1.0-beta.9 (2026-11-01)

- **Agents.** Each project can have named agents: instructions they always
  follow, the connections they may use (some of the project's; shared tools
  only when ticked: the gateway refuses the rest), and their own model, effort
  and autonomy. Ask one in a conversation: "ask the Seaside follow-up agent
  to...".
- **Routines.** A routine runs an agent on a schedule (every day, weekdays,
  chosen days, monthly or every few hours, in your time zone), when something
  happens (its webhook is called on this computer, a file is added to the
  project's folder, a new Airtable record, a new matching email), or when you
  run it. Start from a template: daily digest, new-lead follow-up draft,
  weekly report, data sync check. New routines start paused with a Test run.
  Each run is a task: reads run, actions wait for your approval (in Approvals,
  the routine's page and notifications). Limits per run: steps and minutes, and
  a dollar cap with an API key; and runs a day. After the computer slept, one
  run makes up for the missed ones and says so. Pause one routine, a project's,
  or all of them (the tray's "Pause all routines" now pauses routines only;
  "Stop everything" stays separate). Every run is in the routine's history,
  with an optional summary to your notification channels.
- **Slack and Discord.** Settings > Notifications sets up your own Slack app
  (Socket Mode) or Discord bot, step by step; neither needs anything on this
  computer opened to the internet. Approvals and results arrive as a short
  summary with Approve / Deny / Open buttons. Approve is offered only where
  it's safe without the details (not production changes or deletes); only you
  can press them, and anyone else in the channel is told they're not allowed.
  Open brings Ringkeeper up at that page on this computer. Send test checks it.
- **Choose what each channel sends.** Telegram, Slack and Discord each pick
  approvals, routine results, failures or finished tasks, quiet hours, and
  which clients and projects.
- **Clients of their own.** A client is kept with its name as you typed it
  ("Seaside Windows & Doors"); the short form is only for links. Typing a new
  client anywhere creates it, and a client with only connections, or nothing
  yet, shows in the switcher, Clients & projects ("No projects yet", with Add
  project and its connections count) and every client picker. Clients from
  earlier versions keep their short names until you give them full ones:
  Clients & projects offers to. Rename a client in its Settings, or remove it
  (moving its projects and connections to another client first).
- **Rename a project** in its Settings. Its tasks, approvals and links follow,
  and the old name stays as one of its other names, so asking by it still
  finds the project.

## 0.1.0-beta.8 (2026-11-01)

- **Subscription mode reaches every task.** A task created by answering "which
  project?" (in the app or Telegram) was set to the API key and failed with "No
  Anthropic API key is stored". Tasks now use what's set in Settings when they
  start, wherever they came from.
- **Questions about Ringkeeper itself are answered by Ringkeeper.** "How many
  connections do we have?" went to a client's production database. Questions
  about your connections, projects, clients, tasks, approvals, settings and
  costs are now answered from Ringkeeper's own records: no project, no worker.
- **Asks before production.** When Ringkeeper isn't sure which project you
  mean, or the request would work with a production connection's data, it asks
  you in the conversation before starting anything.
- **Questions in the conversation, with buttons.** "Which project?" and "go
  ahead on production?" show right in the conversation, in plain words, not in
  Approvals and not in its badge. Held changes show there too, and on the task
  page: what it wants to do, the SQL, Approve or Deny with a note. Approving
  anywhere clears it everywhere.
- **Reading never waits.** Only writes wait for approval (changing data,
  sending, deploying, deleting, spending money). A SQL statement that only reads
  runs at once, even on production, and so do MCP tools whose names only read,
  unless they may cost money.
- **Google setup steps include Branding.** Google won't publish your Google
  app until its Branding page has a home page, a privacy policy and an
  authorized domain; the steps in Settings › Google app now say what to enter.
- **A privacy policy**: [PRIVACY.md](PRIVACY.md), also at
  <https://yushkin.us/ringkeeper/privacy>.
- **Test works for Google accounts.** It said "Google returned 404:
  invalid_request" though the account worked: it asked Google at the wrong
  address. Test again after updating to clear "Last test failed". A failed
  test now shows its reason on the connection's row, without opening it.

## 0.1.0-beta.7 (2026-11-01)

- **Updates no longer wait for an unrelated ringkeeper.exe.** The installer
  only waits for programs running from Ringkeeper's own folder, not for any
  program with the same name elsewhere. If one doesn't quit, the message says
  which one ("Ringkeeper is running from …") or which folder is in use.
- **The new icon everywhere.** The "Starting Ringkeeper…" screen and the logo
  beside the name in the app now use it. After an update, the taskbar and Start
  menu now show the new icon too, instead of the one Windows remembered from
  before.
- **Sharp installer images** at any display scale (125%, 150% and up), instead
  of a stretched, blurry icon.
- **A personal project, by Alexey Yushkin.** Settings › About, the license and
  the notices say "Alexey Yushkin and contributors", and Windows' list of
  installed apps names him as the publisher. An update from an earlier version
  still installs into the folder that version was installed in.
- **All projects, except…** A shared tool for all projects can leave out
  clients (all their projects, new ones too) and single projects. The
  connection says so plainly: "All projects except Acme (client),
  seaside-messages". Left-out projects don't see it, and the gateway refuses
  their calls to it. Turning "All projects" off drops the exceptions.
- **Google app, set up once; as many Google accounts as you need.** Settings ›
  Google app says what it is (connecting Ringkeeper to Google once), then shows
  **Add a Google account** and every account signed in, each linking to it.
  You can add more Google apps: a client's own Google Cloud project (for a
  Workspace admin who only trusts their own) or another 100 accounts. Each
  account picks the app it signs in through; the first is the default.
- **Projects from Claude Chat and Cowork.** Besides the folders you opened in
  Claude Code, adding a project now offers the folders you picked in Cowork and
  the Claude app's Code tab, and your Claude projects: each gets a new folder
  with its instructions (as CLAUDE.md) and its files.
- **Several voice languages.** Settings › Voice takes a few languages; each
  recording is detected among just those.
- **Removing asks first, in the app.** Removing a project, a connection or a
  Google app, and Stop everything, ask in place before doing it. The desktop
  window didn't show the browser's confirmation, so one click did it.
- **No scroll bar under the tabs** (Settings and other tabbed pages).
- **Light or dark, your choice.** Settings › Appearance has a Theme: System
  (as before, it follows Windows' light or dark setting), Light or Dark. It's
  kept for this computer, or per browser on a server install.

## 0.1.0-beta.6 (2026-10-31)

- **Every kind of connection can be yours or a client's.** A database, n8n,
  Airtable, Vercel, Twilio, Gmail, GitHub or Google connection, like an MCP
  server, is either a client's (only their projects can use it) or your own
  (any project, or all of them). Add as many of each as you like.
- **GitHub connections**: a token (the form says which repositories and
  permissions to give it), on github.com or your GitHub Enterprise Server. Pin a
  repository per project and the agent can't reach the others. Reading runs;
  issues, comments, pull requests, merges and pushes wait for your approval. A
  task's branch can be pushed and a pull request opened with that project's
  token, never your computer's git credentials.
- **Retell AI** is on the Recommended shelf: Retell's own MCP server with an API
  key. Reading agents, calls and numbers runs; creating or updating agents,
  placing calls and buying numbers wait for your approval; deleting needs the
  name typed.

## 0.1.0-beta.5 (2026-10-31)

- **Shared tools are easier to add.** Add a connection asks first: "A client's
  account or your own tool?", and only a client's account asks whose it is.
  Connections has **Add a shared tool**, which opens the catalog for shared
  tools only.
- **Remote servers can be shared tools** too. The catalog suggests a client's
  account when a server wants a sign-in or a key, and your own tool when it
  answers with nothing; a server that needs nothing connects without a key.
- **Google accounts** (desktop app): Gmail, Drive, Calendar and Sheets for
  your clients' Google accounts and your own, through a Google app you create
  once in your own Google Cloud project (Settings › Google walks you through
  it). Each account is its own connection with the access you choose; a
  client's stays with that client's projects, yours can go to any or all. A
  project can have several, with one as its default. Reading runs; every write
  (sending, sharing, invites, edits, uploads, drafts, the trash) asks you
  first, and you can let a tool run without asking, per account. The Gmail
  app-password connection stays as the simple option.
- **A new icon**: a key ring with three keys, on a navy tile. In the taskbar,
  the tray (with its red dot when something waits for you), the installer, and
  the browser tab. At small sizes the keys are drawn thicker so they stay apart.
- **Check for updates** in Settings › About and in the tray menu. It shows
  this version, when it last checked, and the result: "You're up to date",
  "Update available" with the release notes and **Install and restart**, or
  what went wrong in plain words (offline, a signature that didn't verify).
  While tasks run, it offers **Restart when tasks finish** instead: it never
  restarts mid-task. **Check automatically** (on by default) can be turned off.
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
