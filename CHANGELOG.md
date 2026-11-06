# Changelog

Each release of the desktop app has an entry here; the release workflow
publishes it as the release notes and the app shows it with the update.

## Unreleased

- **Leadfeeder.** Website visitors, companies, contacts and lists, through Leadfeeder's own server,
  with each project held to one account. Reads run, changes wait for approval, and anything that
  may spend credits waits with the cost on the card. Deletes need the name typed.
- **Add a tool to everything at once.** Right after adding one of your own tools, you can use it
  in all projects, with new clients and projects getting it too, and leave out any that shouldn't.
- **Every service has its own mark.** Brands' own logos are bundled with the app, with light and dark
  versions where they exist, and the brand's color with initials where a logo can't be used. They
  show in the catalog and on connection cards.
- **Plan limits are said plainly.** When any service answers that your plan doesn't include
  something, the connection's card and the task say so, the call isn't repeated, and it doesn't
  count as a failed attempt or move the task to a bigger model.
- **"Trust for 1 hour" no longer shows** on services that can't be trusted, like ElevenLabs.

## 0.1.0-beta.25 (2026-11-06)

- **Agents remember between runs.** Each project keeps notes, here by default or in a Notion
  database you can read and edit. An edit there wins, a deleted row is forgotten, and what you or
  other tools add is picked up on the next run. Agents reach only their own project's notes, and
  secrets and card numbers are refused.
- **My memory.** Your own preferences across clients, in a Notion database that works on any
  Notion plan. Agents search it when they start and add to it when they finish. Settings has a new
  Memory tab for it, along with a shared notes page and Notion AI memory, which needs Notion's
  Business plan.
- **Nothing about a client goes in your own memory.** A save naming a client or a project's full
  name is refused, one common word like "website" never counts, and emails, phone numbers and
  street addresses are taken out. A project's own notes may keep a business contact's email.
- **Tools are read again after signing in,** so what a server newly offers shows up.

## 0.1.0-beta.24 (2026-11-06)

- **Long lists have Load more.** Conversations, tasks, activity, routine runs and approvals load 50
  at a time, and nothing is out of reach any more. Search covers everything, not just what's
  loaded, and search and Archived say how many there are. Activity has a search box now, and
  task search also finds the words of a task's conversation.
- **Counts are real.** The Approvals badge and a project's task count no longer stop at 200 and
  100.
- **Spend cards say what it is.** A Google Ads card reads "Budget: Spring promo", "Campaign",
  "Ad group" and "Customer", by Google's own names, with the ID in small text. Other vendors'
  cards say "Invoice", "Transaction" or "Order" where "To or from" and "Account" were wrong.

## 0.1.0-beta.23 (2026-11-06)

- **Social accounts.** Bluesky, Facebook Pages, Instagram, Threads and YouTube can read posts,
  comments and insights. Anything unpublished or scheduled waits for approval. Publishing,
  replying and deleting need the connection's name typed. Your own Meta accounts work now; a
  client's needs Meta's review of your app, and the tile says so.
- **YouTube videos are private unless you choose otherwise.** Ringkeeper always sends the
  privacy, because YouTube's own default is public.
- **Google Ads.** Reports run, and pausing waits for approval. Creating, starting, and changing a
  budget or a bid need the name typed, and the card shows the spend. Settings shows the daily
  operations every client shares.
- **A new Google Ads campaign's card shows what you declare.** Approving one tells Google it has
  no EU political advertising, so the card says that in full. A campaign that has any can't be
  made here.
- **Help > Ask is a conversation.** Ask a follow-up about an answer, or press New question to
  start again. It stays while you open a page from an answer and come back; reloading or
  signing out ends it.

## 0.1.0-beta.22 (2026-11-05)

- **Scanned PDFs and pictures can be read.** The first time a task meets one, the app offers
  the scan reader, a one-time download. It reads English, on this computer, and nothing leaves
  it. If you say Not now, it's in Settings > Reading scans. On a Mac it needs macOS 15 or later.
  Before this, a scanned PDF couldn't be read even with Tesseract installed.
- **Security and Help say plainly** that the desktop app isn't a sandbox: the AI runs as your
  Windows or Mac user, and server mode is the choice for strong isolation.

## 0.1.0-beta.21 (2026-11-05)

- **ElevenLabs,** through its official hosted server. Signing in is all it takes. Reads run;
  agents, calls, voice clones and speech wait for approval; deleting needs the name typed.
- **Intuit, Xero, HubSpot and Salesforce sign-ins** now come back through
  `https://yushkin.us/ringkeeper/oauth/callback`, a static page that hands the browser to
  Ringkeeper on this computer. Add that address to your app at the vendor before signing in
  again. Intuit's production keys no longer end with pasting an address. Accounts already
  connected keep working.
- **Signed links are removed** from logs, the audit log, tool results and conversations:
  Lemon Squeezy customer portal links, S3, Google Cloud and Azure download links, and the like.
- **Long emails and files read in full.** Mail tools for Google, Microsoft, Gmail and Mailbox
  now return the whole message, up to 200,000 characters at a time, as clean text with links
  and tables kept, and the rest in parts. A reply's quoted messages are kept apart. Tasks can
  read attachments (PDF, Word, Excel, CSV, and pictures with OCR) and whole conversations.
  Long files, SQL rows and API answers come in parts too, and a task is told to read the rest
  or to say it has only part.
- **Tasks no longer hold a model key.** Workers call Ringkeeper's model proxy with a token
  that only reaches the model and ends with the session, and Ringkeeper adds the real key on
  the way out, whichever place Claude runs in. Each task's tokens are counted there.
- **Where Claude runs.** Settings › Claude can run tasks, the architect and the router on
  Amazon Bedrock, Google Vertex AI or Microsoft Foundry, with a live check. A project can
  use another place.
- **Clouds.** AWS, Azure, Google Cloud and OCI connect from the login you already have in
  their command line tools. Each runs the cloud's own server on this computer, so
  Ringkeeper holds no key. What a command needs comes from the command itself, and each
  project's link names the client's own subscription, project or compartment.
- **Oracle Database,** with its wallet for an Autonomous Database. The same checks as
  Postgres, and a plain update or delete can be undone.

## 0.1.0-beta.20 (2026-11-05)

- **Business systems.** HubSpot, Salesforce, Pipedrive, Keap, Jobber, Housecall Pro,
  ServiceTitan, QuickBooks, Xero, Wave, Business Central, BILL, Shopify, Ghost, Square,
  Helcim, Lemon Squeezy, Clover, Braintree, Plaid and Clerk, plus official servers for
  Stripe, PayPal, Webflow, Sanity, Contentful and more. Each built-in one has only the
  actions Ringkeeper knows, so what isn't listed can't be called.
- **Microsoft accounts.** Mail, calendar, OneDrive and SharePoint files, through your own
  Entra app, with the same holds as Google.
- **Settings › Developer apps** holds your own registered apps for Google, Microsoft,
  HubSpot, Salesforce, Xero, Intuit and Jobber, with the steps, the address to paste, the
  scopes and a live check. Many accounts sign in through one app.
- **Trusting an action for an hour now covers that one action.** On an add-on tool it used
  to cover everything else that connection could do without the name typed.
- **An approval that moves money shows it in large text:** amount, currency, counterparty
  and account, as written in the call.
- **Rules that can't be loosened.** On CRM, accounting, CMS and payment servers a per-tool
  setting can make a tool stricter, never looser. Nothing on an accounting connection runs
  by itself.
- **Gmail and Google accounts follow the same email rule as every other connection.**
  One email waits for your approval, and more than 10 people at once needs the connection's
  name typed, never from a chat app. A Google account can no longer let sending run without
  asking; a setting that did stops counting.
- **The Forms On Fire tile is marked Unverified** until its address and sign-in are
  confirmed.

## 0.1.0-beta.19 (2026-11-05)

- **More connections.** Email sending through any SMTP provider or Resend, any mailbox
  (Gmail, Outlook, Zoho, Yahoo or your own IMAP server), any HTTP API (with Forms On Fire
  and DataForSEO), and file storage (AWS S3, Cloudflare R2, Vercel Blob). Google accounts
  can read Search Console and Analytics. Connectors are now grouped by what they're for,
  with Built in, Official and Community marks.
- **Use a connection in all of a client's projects.** Projects added later get it too,
  with exceptions if you want, and a project's link can still be narrowed.
- **Ringkeeper finds what you already have.** Logins on this computer (GitHub CLI, Vercel
  CLI, and AWS, Azure, Google Cloud and OCI, listed for now), MCP servers in a project's
  folder and in Claude Code, Codex and Cursor settings, and many more keys in `.env` files.
  Each is offered once and added only when you say.
- **Card and bank numbers stay out.** A tool call carrying one is refused, and they're
  removed from logs, conversations, a task's events and the audit log.
- **Voice on Intel Macs.** The Intel build can now download voice like the others. Older
  processors take longer to turn speech into text.
- **Cmd+Q stops all of Ringkeeper on a Mac.** The background part kept running, so a newly
  installed version could connect to it and go on showing the old version number. A new
  version now also replaces whatever an older one left running.
- **Opening it on a Mac.** The app isn't signed yet, so the first time, in Finder, choose
  Go › Applications, right-click Ringkeeper, and choose Open. The install guide and the
  download page now say so.

## 0.1.0-beta.18 (2026-11-04)

- **Older Macs.** The app's pages are now built for the web engine in macOS 12, so they
  no longer risk stopping at Loading there. The build fails if that ever changes.
- **Your Claude Code login on a Mac.** "Or use my Claude Code login" now works on Macs too,
  reading it from your Keychain. macOS asks you to allow that.
- **Changes are saved before the app answers.** A change could take a moment longer to
  save than the app took to reply, so the next screen could briefly show the old value.

## 0.1.0-beta.17 (2026-11-04)

- **Intel Macs.** There's now a download for Macs with an Intel processor, beside the
  one for Apple Silicon. Voice isn't available there: the speech engine no longer
  supports them. Like the other Mac build, it's unsigned and experimental.
- **Replies read cleanly everywhere.** Task cards, Telegram, Slack and the digest showed
  Claude's formatting as raw stars and backticks. Now they show plain words.
- **Checkboxes keep their size.** Next to long text they were squeezed narrower than
  the others.
- **A new picture of how it works.** Help and the guide now start from what you actually
  do: you ask, Ringkeeper finds the project, Claude does the work, and live changes wait
  for you. Agents and routines are shown as the optional extras they are.

## 0.1.0-beta.16 (2026-11-04)

- **The filter says when it's on.** With a client or project chosen in the top bar,
  Home, Tasks, Approvals, Agents and Connectors now say "Showing Acme Windows only" with
  Show everything beside it, so nothing looks gone. The last 24 hours follows the filter
  too, and Approvals no longer says nothing is waiting when other clients have requests.
- **Client connections add up.** A client now counts your own tools its projects use,
  and its page lists them, so a project with a connection no longer sits under
  "0 connections".
- **A clearer light theme.** Cards stand out from the page, quiet text is darker, and
  boxes you type in or choose from have a border you can see. In the dark theme too.
- **New projects reach the switcher.** The switcher and the client counts refresh as you
  move between pages and come back to the window, without restarting the app.

## 0.1.0-beta.15 (2026-11-03)

- **Project names.** A project now has a name as you typed it, shown everywhere: pages,
  approvals, messages and the digest. The short name stays for links and the AI, and
  moves to Project › Settings. Existing projects get a name from their short one:
  change it under About this project.
- **Logos.** Connectors show each service's logo (from Simple Icons), and initials
  where there is none.
- **Hide the menu.** The sidebar's toggle is now just the panel icon.
- **Quick request.** Ctrl+Alt+R opens a small box over whatever you're doing, with
  voice and the client and project switcher. Send, and it shows a link to the
  conversation and closes. Change the key in Settings › Advanced.
- **@-mentions.** Type @ in a request box to name a client, project, connection or
  agent. The request goes there, whatever else it says.
- **Keys already in a project.** A project's .env and config files are offered as
  connections: masked, one confirmation each, never from outside its folder.
- **Connection health.** Every connection is tested daily, reads only. You hear if one
  stops working, or 7 days before a key expires. Test all is on Connectors.
- **Faster approvals.** J, K, A and D on the keyboard, Approve all N like this, and
  trusting an action in a project for an hour. Never for deletes.
- **Morning digest.** Home shows what ran, failed and waits, per client; Slack,
  Telegram and Discord can get it at a set time.
- **Run again, and saved requests.** On every task. Saved requests are on Home.
- **Files on a request.** Drop them on the box: they go into the project.
- **Pins and recents.** In the switcher and Ctrl+K.
- **Help that knows the guide.** Ask how something works in the request box or in
  Help › Ask. It answers from this version's guide and your setup, says so when
  something doesn't exist, and ends with the next step.

## 0.1.0-beta.14 (2026-11-03)

- **Connectors.** Connections is now Connectors, and Browse connectors opens the
  directory: built-in services first, as tiles that open their form; then
  services you sign in to; then a search of every other connector, with
  publishers and verified badges, the service's own first and community servers
  folded away.
- **Is this live client data?** Asked every time you add a client's connection,
  with no default, and it says what each answer lets run.
- **Readable names.** Connections read like "n8n · n8n.geninfos.com" or "Google ·
  name@gmail.com", on every page and approval. Existing ones are renamed, and you
  can change a name under Details › Change. Task transcripts read like "Database:
  execute".
- **Linking in one click.** A connection's card says where it's used and has Link
  to project…; a project's Connections tab has Link a connection. A client with
  no projects says so, with Add project and From Claude.
- **From Claude, tidied.** Most recent first, grouped by likely client, with a
  search. A Claude project named like a folder shows once, and folders that only
  hold other projects are folded away.
- **Your subscription first.** With personal subscription mode on, first run and
  Settings › Claude start with your subscription.
- **Google accounts in one place.** Settings › Google app lists each account with
  its client and projects, and Add a Google account is right there. New accounts
  can write drafts unless you untick it.
- **Results where you look.** A task's card and a routine's run show what it said
  at the end. Tasks offers to archive failed tasks older than 7 days.
- **No dead ends.** Agents and routines offer Add project when there are none, and
  a project's new routine offers Create an agent right there.
- **Deep folders.** A task no longer fails with "Filename too long" in a deeply
  nested project folder on Windows.

## 0.1.0-beta.13 (2026-11-02)

- **Formatted replies.** Ringkeeper's replies, task transcripts and questions
  show their bold, lists, tables, code and links instead of the raw `**` marks.
- **Room to work.** Pages use the window's width (up to a comfortable limit)
  instead of a narrow column. The menu can shrink to icons only: Icons only at
  its foot, and it stays that way.

## 0.1.0-beta.12 (2026-11-02)

- **The tour stays skipped.** Skipping or finishing the tour no longer starts
  it again each time you go back to Home.
- **Every Settings tab in view.** About was cut off at the end of the tab row;
  the tabs now fit, and wrap onto a second row in a narrow window.
- **New examples.** Forms and Help use General Informatics LLC and
  geninfos.com as their examples.
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
