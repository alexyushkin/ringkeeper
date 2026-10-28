# Ringkeeper

Ringkeeper lets one person run AI agents across many clients without ever
switching connections.

**[Download for Windows](https://github.com/alexyushkin/ringkeeper-releases/releases/latest)**

Windows 10/11. Beta, unsigned: SmartScreen will warn, click **More info ›
Run anyway**.

Prefer a server? See [docs/INSTALL.md](docs/INSTALL.md) (Docker).

If you work for 100 clients, each has its own database, Airtable, Vercel, n8n
and email. With Claude Desktop or Claude Code you reconnect your tools every
time you switch clients. In Ringkeeper you connect each client's tools once,
attach them to that client's projects, and forget about it.

Then just ask: "Add last week's leads to Acme's Airtable." Ringkeeper figures
out which client and project you mean, uses that project's connections, and
picks the right model for the job.

- Connect once per client, use in every project that needs it
- Requests go to the right project automatically
- One client's connections can never reach another client's project
- Changes to live data wait for your approval; everything is logged
- Runs on your own computer, with your own Claude API key

Free and open source (MIT). Windows. Beta.

## The idea: instances bound to projects

If you work for several clients you probably have several accounts of the same
service: three Supabase projects, two n8n hosts, a couple of Vercel teams. An
agent that can reach "Supabase" can reach all of them, and one wrong project
ref is all it takes to write to the wrong client's database.

Ringkeeper models each account as an **instance** and binds instances to
**projects**:

```
project acme-web ──┬── acme-supabase-dev    (default supabase)
                   ├── acme-supabase-prod   (bound, never default)
                   └── acme-n8n             (bound)

project globex-portal ── globex-supabase-dev (default supabase)
```

A worker running for `acme-web` can only ever reach `acme-*` instances. The
`globex-*` instances don't exist from its point of view.

In the web app a connection belongs to one client: it's added once, in that
client's library, and linked to any of that client's projects (never another
client's). A link can be narrowed: for example acme-web to one Neon project
and branch, and acme-app to another, over the same connection. The gateway
enforces it by argument name: an ID the link pins is filled in when a call
leaves it out and refused when a call names another, and tools that work
across the whole account are refused on a narrowed link.

Services with an official remote MCP server (Neon, Supabase, Airtable,
Sentry, Notion, Linear, Cloudflare, Atlassian) connect with one click: the
service's own sign-in page, using MCP's OAuth with dynamic client
registration. Tokens go to the vault and are refreshed before they expire.
Pasting a token stays available, and is the only way for GitHub. Notion and
Linear can't be narrowed below the workspace you sign in to. In server mode,
sign-in needs `RINGKEEPER_WEB_ORIGIN` (the address the service sends you back
to). SQL run through a remote service's SQL tool is checked like a database
connection's: deleting or changing tables needs the connection's name typed.

These services are the **Recommended** shelf of the catalog, because they have
built-in rules (narrowing, SQL checks). Below it, the catalog searches every
server in the official [MCP Registry](https://registry.modelcontextprotocol.io)
and keeps what it has seen in the data folder, so search still works offline.
Each result shows its publisher, as the registry verified it (a domain, or a
GitHub account), whether it's remote or runs on your computer, and whether
one-click sign-in works (Ringkeeper asks the server, and remembers for a week).

- **Remote servers** connect with MCP's OAuth and dynamic client registration,
  as above, or with the token or header the server asks for.
- **Local servers** (npm, PyPI or container packages) install when you ask,
  into the data folder (`mcp-packages`), pinned to the version shown. One
  confirmation screen names the publisher, the package and version, and the
  commands that install and run it; installing needs exactly what was shown.
  npm install scripts are off. Nothing updates by itself: when the catalog has
  a newer version, the connection offers it, with the same confirmation. They
  need Node.js, uv or Docker on the computer, and say so if it's missing.
- **Add by address:** any remote MCP server that isn't in the registry.
- **Install extension:** a Claude Desktop extension file (`.mcpb`, or the older
  `.dxt`). One confirmation screen shows what its manifest says: the name and
  version, the publisher (as the extension states it; nobody has checked it),
  its tools, the command it runs and anything this computer is missing for it
  (Node.js or Python, say), and the settings it asks for. Installing unpacks
  that same file (named by its SHA-256) into the data folder
  (`mcp-packages/mcpb`); a file with paths that would land outside its folder,
  links, or a platform other than this one isn't installed. Like an imported
  extension, it's added as Test data.
- **Claude Desktop import** stays: its config file and installed extensions.

A server that is one of the recommended services (matched by its address)
connects as that service, with its rules. Every other server gets the generic
rules: its MCP annotations decide what runs without asking (read-only tools),
and a tool without annotations waits for approval, like any write. Test or
Production is chosen when connecting, and every connection still belongs to a
client and is linked to that client's projects.

## How a call flows

```
worker (Claude Code, …)
   │  MCP over HTTP, Authorization: Bearer <worker token>
   ▼
gateway  /mcp/database  /mcp/n8n  /mcp/airtable  /mcp/vercel  /mcp/twilio  /mcp/gmail  /mcp/mcp
   1. valid token?            (signed, short-lived, names project + session)
   2. instance exists?        (omitted only for reads: the project default)
   3. bound to the project?
   4. tool in allowed_tools?  (empty = all)
   ├─ read                  → run
   ├─ write, dev/staging    → run, audit
   ├─ write, prod           → hold as a decision, audit
   │                           unless the tool is in preapproved_writes
   ├─ send (a text, email)  → always hold, even on test data
   └─ draft (Gmail)         → run, audit
   ▼
adapter (one per service)  ← secret resolved from the vault, never returned
```

The first failed check refuses the call with a message the agent can relay.
An agent gets the same message for an instance that doesn't exist and one that
belongs to another client, so it can't probe for other clients' names. The
audit log records the precise reason.

## Rules

- **Secrets never live in the registry.** An instance stores a `secret_ref`
  pointer (`file://name` or `aws-sm://name`); the value is in the vault.
- **Secrets never come back out.** Tool results, errors, log lines and audit
  rows are scrubbed of every resolved secret, including the password inside a
  connection string. A call whose arguments contain a secret value is refused.
- **One default per type, and prod is never a default.** Prod writes must name
  the instance explicitly.
- **Prod writes wait for a human** unless the binding pre-approves that tool.
  A held write becomes a *decision*. It expires after 24h (configurable). On
  approval it is re-checked against the current registry before it runs.
- **Writes that destroy data need the connection's name typed.** Deleting
  rows, dropping or altering objects, anything the SQL check can't read
  (`DO $$`, function calls, `E''` strings) and any n8n tool other than
  activate/deactivate counts. The rule is worked out again when the write is
  approved and when it runs, from the stored arguments, so a tampered flag
  can't skip it. Telegram can deny these but never approve them.
- **Connections can't point at Ringkeeper itself**: not at its own services
  (Postgres, api, gateway, web, stt), loopback, link-local or cloud metadata
  addresses, by name or by what the name resolves to, checked when saved and
  again when connecting. LAN addresses are fine. For a service published on
  the same machine during development, set
  `RINGKEEPER_ALLOW_LOCAL_CONNECTIONS=1`.
- **Bound instances are protected.** Removing an instance that a project still
  uses fails unless you pass `--force`. Removing a connection removes its
  secret from the vault.

## The vault and the master key

**Losing `master.key` means losing the vault and every worker token.**
Ringkeeper has one 32-byte master key. It derives two keys from it: the key
that encrypts the `file://` vault, and the key that signs worker tokens.
Without the master key the vault can't be decrypted, and every outstanding
token stops verifying. There is no recovery: you would have to re-enter every
secret and issue new tokens.

The key lives in `RINGKEEPER_MASTER_KEY` if that is set. Otherwise it lives in
`<data dir>/master.key`, which the api creates on its first start (or the CLI,
if it runs first). The data dir is `./.ringkeeper` by default and holds
`vault.json` too. The setup wizard gives you this key as your **recovery key**.

**Back it up** like any other root credential:

- Copy `.ringkeeper/master.key` (or your `RINGKEEPER_MASTER_KEY` value) into a
  password manager or a secrets store you already trust. A backup of
  `vault.json` is useless without it.
- Back up `.ringkeeper/vault.json` whenever you change secrets. It is encrypted,
  so it can go with your normal backups, as long as the key isn't stored
  beside it.
- To restore, put both files back in the data dir, or set
  `RINGKEEPER_MASTER_KEY` and restore only `vault.json`.

**Workers must never receive the master key or the data dir.** Only the
gateway and the CLI read them. The compose file mounts `.ringkeeper` into the
gateway read-only and into nothing else. Don't mount it into a worker's
container or workspace, and don't put `RINGKEEPER_MASTER_KEY` in a worker's
environment. A worker gets exactly one thing: a short-lived token for its
project. The gateway refuses to start without an existing key, so it can
never silently mint a new one and orphan the vault.

```bash
printf '%s' 'postgresql://…' | uv run ringkeeper secret set file://acme/supabase-dev
uv run ringkeeper secret set aws-sm://ringkeeper/acme/n8n   # hidden prompt
uv run ringkeeper secret check acme-supabase-dev           # never prints the value
```

There is deliberately no way to pass a secret value as an argument, because
argv ends up in shell history. `aws-sm://` uses the standard AWS credential
chain.

## Gateway

There is one MCP endpoint per service type, served over Streamable HTTP on
`127.0.0.1:8100`. Every tool takes an optional `instance` argument.

| Endpoint | Reads | Writes |
|---|---|---|
| `/mcp/database` (also `/mcp/supabase`) | `list_tables`, `describe_table`, `query` (READ ONLY transaction) | `execute` |
| `/mcp/n8n` | `list_workflows`, `get_workflow`, `list_executions`, `get_execution` | `activate_workflow`, `deactivate_workflow`, `update_workflow`, `retry_execution`, `stop_execution` (all always held) |
| `/mcp/airtable` | `list_bases`, `list_tables`, `list_records`, `get_record` | `create_records`, `update_records`, `delete_records` |
| `/mcp/vercel` | `list_teams`, `list_projects`, `list_deployments`, `get_deployment`, `get_build_logs`, `list_env_names` | `redeploy`, `promote`, `rollback` |
| `/mcp/twilio` | `list_phone_numbers`, `list_messages`, `get_message`, `list_calls` | `send_sms` (always held) |
| `/mcp/gmail` | `search_messages`, `get_message` | `create_draft` (never held), `send_email` (always held) |
| `/mcp/mcp` | `list_tools` | `call_tool` (held unless the tool is read-only, by its annotations or your choice) |

A worker only gets the endpoints of the services its project is connected to.

- **supabase:** the secret is a Postgres connection string. Each call runs
  exactly one statement over the extended protocol, so `COMMIT; …` tricks
  can't escape a read-only transaction.
- **n8n:** the secret is the API key and `base_url` is the host. Every write
  waits for approval, even on test data. n8n's API can't run a workflow on
  demand; `retry_execution` runs a past execution again.
  `update_workflow` never puts a live workflow's new version live unless you
  pass `republish=true`. A held update is refused at approval if the workflow
  changed after the hold (its `updatedAt` moved), so an old approval can't
  overwrite newer edits.
- **airtable:** the secret is a personal access token; its own scopes and base
  list limit what it can reach. Updates and deletes need the connection's name
  typed to approve on production; creates don't.
- **vercel:** the secret is an access token. Env variables come back as names
  only, never values. The only writes put an existing build live: redeploy,
  promote, roll back. No env changes, domains or purchases.
- **twilio:** the secret is JSON with the Account SID and a Standard API key
  (SID and secret). Sending a text always waits for approval.
- **mcp:** an MCP server from the catalog, added by its address, or imported
  from Claude Desktop (desktop app, Settings or first run). How to start it (command, args, env; or URL and headers) is
  read from Claude Desktop's file straight into the vault; the connection keeps
  only a preview with secret-looking args masked. A command server runs on the
  gateway's machine for each call, with a small environment (no Claude
  credential, nothing of Ringkeeper's) in its own folder. Each tool's default
  comes from the server's MCP annotations: `readOnlyHint` runs without asking,
  `destructiveHint` needs the connection's name typed to approve, and anything
  else waits for approval (with no annotations, a tool whose name says it
  deletes needs the name typed). The annotations are remembered whenever the
  tools are listed (Test, Connections, or the agent) and a change to them is
  audited. Override any tool under Connections › Tools and approvals.
- **gmail:** IMAP and SMTP with an app password (no Google OAuth); the secret is
  JSON with the address and the app password. Reading never marks mail as
  read. Drafts run without asking; sending always waits for approval.

### Worker tokens

```bash
uv run ringkeeper token issue --project acme-web --ttl 1h
```

This prints a signed token that names the project, a session id and an
expiry; its `jti` goes to stderr. The gateway trusts nothing else about the
caller. Tokens are verified on every request, so an expired token stops
working mid-session.

`ringkeeper token revoke <jti>` kills a token before it expires. The gateway
then refuses it and audits the refusal. Worker sessions revoke their own token
automatically when they end, whatever the outcome.

### Decisions and the audit log

```bash
uv run ringkeeper decision list                 # pending by default
uv run ringkeeper decision show <id>            # exact args it would run
uv run ringkeeper decision approve <id>         # re-check, run, record result
uv run ringkeeper decision deny <id> --reason "…"
uv run ringkeeper audit list --project acme-web --outcome held --since 1d
```

`approve` runs the stored call from the process you run it in, so it needs
network access to the instance. For the compose demo, run it inside the
gateway container (shown below).

The audit log gets a row for every write attempt and every refusal. Each row
holds the time, project, session, instance, tool, outcome (`refused`, `held`,
`executed` or `failed`), redacted args with their hash, and a truncated
result. The database itself rejects any `UPDATE` or `DELETE` on it.

## Workers

A task is a prompt for one project. The session manager runs it in a worker
container: the Claude Agent SDK, working in the project's workspace, with the
gateway as its only MCP servers.

```bash
uv run ringkeeper auth login             # store an Anthropic API key (hidden prompt)
uv run ringkeeper auth status            # mode, and whether the credential resolves
uv run ringkeeper task run --project acme-web "Summarise the acme customers"
uv run ringkeeper task list
uv run ringkeeper task show <id>         # status, turns, cost estimate, changes, last messages
uv run ringkeeper task resume <id> "Now add that to NOTES.md"
uv run ringkeeper task stop <id>
uv run ringkeeper stop-all
```

`task run` follows the task until it ends; `--detach` returns at once, and
Ctrl-C detaches without stopping it. Limits per task: `--max-turns` (default
30), `--max-runtime` (default 30m; the container is killed and the task marked
`timed_out`), and `--budget` in USD. The budget is the SDK's own cost
estimate, so leave headroom. A global cap (`RINGKEEPER_MAX_CONCURRENT_WORKERS`,
default 4) queues anything beyond it.

**What a worker gets, and nothing more:**
- the project's workspace, read-write at `/workspace`. The workspace path is
  absolute, or relative to `RINGKEEPER_WORKSPACES_ROOT` as the Docker host sees
  it; Windows paths are translated for Docker Desktop.
- a per-task volume for the SDK's own state, so `task resume` continues the
  same Claude session (one per role: architect sessions and worker sessions
  share no files). It holds transcripts, so it is removed once the task has
  been finished for `RINGKEEPER_RETENTION_DAYS`.
- a gateway token scoped to the project, valid for the task's max runtime and
  revoked when the session ends
- exactly one Claude credential: `ANTHROPIC_API_KEY` in `api_key` mode. Its
  environment is built from an allowlist, so nothing that could switch billing
  (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `ANTHROPIC_BASE_URL`, …)
  is ever inherited.

It never gets the master key, the data dir or the Docker socket. It has a
read-only root filesystem and memory and process limits. The runner starts
as root with only four capabilities (setuid, setgid, kill, read-search); the
agent, git and the project's checks all run as an unprivileged `worker`
user. It is on a network that holds only the gateway, so it can't reach
Postgres or the api by name. Settings files (user, project, local) aren't
loaded, and the project's `autonomy` removes tools: `strict` has no Bash and
no web access, `standard` has no web access.

**Git:** if the workspace is a git repo, the worker commits on
`ringkeeper/<task-id>` and never moves `main`. If the agent moves `main`
anyway, it's put back and the moved commits are kept on a side branch. Only
files the worker touched are committed, never your own uncommitted edits.
Nothing is pushed. Without a repo, the task records the list of changed files.

**Nothing planted for your machine.** The workspace is your real folder, and
git runs hooks and commands from `.git` on *your* machine later. So after the
agent (and after the checks), the runner kills anything the agent left
running, then puts `.git/config`, `.git/hooks` and `.git/info` back exactly as
they were (the task's events say what was undone). Its own git commands never
run hooks. New `.git` folders deeper in the workspace are reported: check
them before running git there.

**Workspaces set from the web app** must be a folder inside
`RINGKEEPER_WORKSPACES_ROOT` (no absolute paths, no `..`): a worker mounts
it, so an absolute path could expose anything on the host. The CLI and the
registry can still set an absolute path.

**The session manager** lives in the api service. It is the only service that
mounts the Docker socket, which makes it root-equivalent on the host: guard
the api like root. Build the worker image once with
`docker compose --profile worker build worker`.

### Known risks (not solved yet)

- **The worker holds the Claude credential in its environment** (the agent
  needs it to call Claude). A prompt-injected worker with Bash could read it
  and send it out. Egress allowlisting comes later; until then, prefer
  `strict` autonomy (no Bash) for anything that reads untrusted content, and
  use an API key from a workspace with a spend limit.
- **Workers have open internet access**, which they need for the Anthropic
  API.
- **Costs are what the worker reports.** Budgets and the cost estimate come
  from the SDK inside the worker; a hijacked worker could report less than it
  spent. Anthropic's own spend limits (in the console) are the real cap.
- **What the agent writes is its work.** Ringkeeper protects `.git`, but a
  changed `Makefile`, `package.json` script or test file runs whatever it says
  when you run it. Read the task's diff before running the project's code.
- **Host-published ports are reachable by IP.** On Docker Desktop, the
  host-gateway address forwards to the host's loopback, so a worker that knows
  that address could reach what's published there: the api, gateway and web
  app (each needs a token), and Postgres if you added `docker-compose.dev.yml`.
  The host name `host.docker.internal` is blocked, but the IP isn't. Keep a
  strong `POSTGRES_PASSWORD`; Ringkeeper warns at start if it's weak.

### Subscription mode: personal use only

`api_key` is the default. The setup wizard offers subscription mode only
when `RINGKEEPER_ALLOW_SUBSCRIPTION=1` is set (or the hidden toggle at
Settings#personal is on), labeled personal use only. Anthropic's Agent
SDK terms don't allow offering claude.ai (subscription) login in products
built on the SDK without Anthropic's approval. Subscription mode therefore exists only for running
Ringkeeper yourself on **your own** Claude account. Never use it to run work
for anyone else.

If that's your case, set `RINGKEEPER_ALLOW_SUBSCRIPTION=1` in `.env`; setup
and Settings then offer your subscription next to the API key, with a token
from `claude setup-token`. (From the terminal instead: set
`RINGKEEPER_CLAUDE_AUTH_MODE=subscription` and run
`ringkeeper auth login --subscription`.) An install that already uses its
subscription keeps working without the setting; Settings just asks for it
before you can change it. Workers then get
`CLAUDE_CODE_OAUTH_TOKEN` and never `ANTHROPIC_API_KEY`, so billing can't
switch silently. Budgets don't apply in this mode.

## Messages, the router and the inbox

Instead of picking a project yourself, you can just say what you want:

```bash
uv run ringkeeper ask "In acme-web add a plan formatter, with tests. Also how many customers has globex?"
uv run ringkeeper ask --thread <id> "and the same for acme"
```

The **router** (in the api service, `RINGKEEPER_ROUTER=1`) handles one message
at a time with one Claude call and every tool turned off. It sees the project
cards (name, owner, description, aliases, bound instance names and types), the
thread so far, open tasks, pending decisions and saved routing examples, and
never secrets. It answers with a list of tasks, each with a project, a
confidence, a kind (`feature`, `fix`, `data`, `content`, `ops`), a risk
(`read`, `write`, `prod-write`), a complexity (`low`, `medium`, `high`), an
outcome and the worker prompt. The answer is validated (one retry on invalid
output), and a task is never given to projects with different owners: it must
be split.

A task that touches one project with a confidence of at least 0.8
(`RINGKEEPER_ROUTER_CONFIDENCE_THRESHOLD`) is dispatched. Anything else becomes
a **route decision** with the top two candidates. Your answer is saved as a
routing example: the same message (ignoring case and spacing) then routes
without asking, and similar ones get it as a hint. Remove a wrong one with
`ringkeeper routing examples list` and `delete <id>`.

The **inbox** holds every decision waiting for you:

| Kind       | What                                  | Answer                          |
| ---------- | ------------------------------------- | ------------------------------- |
| `route`    | which project a task belongs to       | a project, or `none`            |
| `approve`  | a held prod write                     | `approve` / `deny`              |
| `plan`     | an architect's plan (feature tasks)   | `approve` / `deny` (+ `--note`) |
| `question` | a worker asked you something          | your answer, as text            |

```bash
uv run ringkeeper inbox list
uv run ringkeeper inbox show <id>       # full details: plan text, held arguments
uv run ringkeeper inbox answer <id> approve
uv run ringkeeper inbox answer <id> "Use the plans table"
```

Workers get an `ask_user` tool. Calling it ends the session with status
`needs_input` and opens a question; answering resumes the same Claude session
with your answer. Every answer is audited with who answered and how (`cli`,
`api` or `telegram`). Approving a held write from the CLI runs it from your
machine; from the API or Telegram, the gateway runs it, since it can reach the
instances.

## The architect loop (feature tasks)

A `feature` task starts with an **architect** session, not a worker, unless
the router rated it low complexity (a small, clear change such as adding a
file): those go straight to a worker on the low-tier model. The architect runs
in the worker image with the workspace mounted read-only and no write tools
(no Write, Edit or Bash). Its token is an architect token, which only opens
the gateway's `/mcp/architect` endpoint: a worker token can't call it, and an
architect token can't reach the service endpoints.

1. The architect reads the code and calls `submit_plan(title, plan,
   worker_prompt)`. The plan becomes a `plan` decision.
2. Once you approve, it calls `dispatch_worker(prompt)`, and a worker builds
   on the task branch.
3. After each worker turn, the runner runs the project's `checks` inside the
   worker container, without the Claude credential or the gateway token in
   their environment, and reports them with the branch diff. The architect is
   resumed with both and calls `approve_work(summary)` or
   `continue_worker(feedback)`, which resumes the same worker session. It
   can't approve while checks fail.
4. After 3 reviews, `continue_worker` asks you instead (a `question`).
   Answering grants one more round.

Each tool call records one step and ends the architect's turn, so the
architect holds no container or slot while a worker runs. On approval the task
succeeds with the branch ready and a summary. Nothing is pushed or merged.

Add checks per project in the registry:

```yaml
  - name: acme-web
    checks: ["python -m pytest -q -p no:cacheprovider"]
```

The worker image includes pytest and uv.

## Models

| Role                          | Default             | Setting                       |
| ----------------------------- | ------------------- | ----------------------------- |
| router                        | `claude-haiku-5-5`  | `RINGKEEPER_MODEL_ROUTER`     |
| architect                     | `claude-sonnet-5-5` | `RINGKEEPER_MODEL_ARCHITECT`  |
| architect, `high` complexity  | `claude-opus-5-5`   | `RINGKEEPER_MODEL_HIGH`       |
| worker (no complexity)        | `claude-sonnet-5-5` | `RINGKEEPER_MODEL_WORKER`     |
| worker, `low` complexity      | `claude-haiku-5-5`  | `RINGKEEPER_MODEL_LOW`        |
| worker, `medium` complexity   | `claude-sonnet-5-5` | `RINGKEEPER_MODEL_MEDIUM`     |
| worker, `high` complexity     | `claude-opus-5-5`   | `RINGKEEPER_MODEL_HIGH`       |

The router sets each task's complexity: `low` features skip the architect, and
`high` ones plan on the high-tier model. After two failed check rounds in the
architect loop, the worker moves up one tier. A project can override any of
these in the registry (`models: {architect: ..., low: ...}`), and each session
records the model it ran on (`ringkeeper task show`).

Each role also has an effort setting (`RINGKEEPER_EFFORT_ROUTER`,
`RINGKEEPER_EFFORT_ARCHITECT`, `RINGKEEPER_EFFORT_WORKER`: low, medium, high,
xhigh or max). A worker's effort follows its task's size, never above its
setting; a task without a size gets the setting. In API key mode every task
has a spending cap (`RINGKEEPER_TASK_BUDGET_USD`, $1 by default) across all
its sessions: at the cap the task pauses and asks whether to give it that much
more. Settings in the web app lists Anthropic's models (recommended first)
and changes all of these, and the maximum steps per task, without a restart.

## Telegram

Telegram bot chats aren't end-to-end encrypted, so Ringkeeper treats Telegram
as a **notification channel** only. Each new decision goes out as a short,
scrubbed message: kind, project, instance and a description, with buttons.
Approvals, plans and routes get one line (at most
`RINGKEEPER_TELEGRAM_SUMMARY_CHARS`, default 160). A worker's question gets
more room (`RINGKEEPER_TELEGRAM_QUESTION_CHARS`, default 1000) and keeps its
line breaks, since you have to be able to read it to answer it. Neither ever
includes SQL, tool arguments, diffs or plan text: SQL statements and code
blocks in a question are replaced with "[SQL: see the app]". Anything cut or
hidden ends with "Shortened for Telegram. The full text is in the Ringkeeper
app." The full details stay in the CLI and API.

- held writes and plans: **Approve** / **Deny**
- routes: one button per candidate, plus **None of these**
- questions: reply to the message with your answer, typed or as a voice note
  (see [Voice input](#voice-input))

The setup wizard (or **Settings → Telegram**) connects it: paste the bot token
from @BotFather, message the bot, pick your chat. From a terminal instead:

```bash
docker compose exec api ringkeeper telegram login   # the bot token (hidden prompt)
# message the bot from your phone, then:
docker compose exec api ringkeeper telegram chats   # find your chat id
# .env: RINGKEEPER_TELEGRAM=1 and RINGKEEPER_TELEGRAM_CHAT_IDS=[123456789]
docker compose up -d                                # the api service runs the bridge
docker compose exec api ringkeeper telegram test
```

Only chats in `RINGKEEPER_TELEGRAM_CHAT_IDS` get notifications, and button
presses or replies from any other chat are ignored. Anyone in an allowed chat
can answer, so allow only your private chat with the bot, or groups you trust.

### Requests from Telegram

By default the bot only takes answers. Any other message gets a hint that
requests are turned off, and nothing runs. With `RINGKEEPER_TELEGRAM_REQUESTS=1`,
a message that isn't an answer is a new request. It goes through the same
router as `ringkeeper ask`, recorded with `source=telegram` and `input=text`
or `voice`. The bot then sends short notices: what the router did (projects,
task ids and kinds) and when each task ends (project, id, status). Results and
summaries stay in the CLI and API.

> **Privacy:** turning this on sends your client requests, typed or spoken,
> through Telegram's servers. Telegram bot chats aren't end-to-end encrypted,
> so Telegram can read them. Leave it off for anything you wouldn't put in a
> regular Telegram chat.

### Voice input

In the desktop app, hold the mic in the request box. The first time, it offers
to download voice: the speech engine (faster-whisper and its libraries, from
PyPI, each checked against the hash the installer lists) and the speech model:
Fast (faster-whisper's `tiny`, about 160 MB in all) unless you choose Accuracy
(its `small`, about 570 MB) in Settings › Voice. The size is shown before
anything downloads, then its progress; only the chosen model is kept, and the
installer stays smaller for everyone who never uses voice.
Speech becomes text on your computer: the recording never leaves it, and is
dropped once it's transcribed, with the same limits as the server's `stt`
service. From source, install the engine with `uv sync --extra voice`.

With the optional `stt` service running, you can reply to a question with a
voice note, and, if requests are on, send a request as one. The bot
transcribes it and answers **I heard: ...** with **Confirm** and **Redo**.
Confirm answers the question (or sends the request) exactly as if you had
typed the text, recorded as `input: voice`. Redo throws the transcript away,
and your next voice note (or typed message) answers the same question, so you
don't need to use Telegram's Reply again. Unanswered confirmations expire after
10 minutes (`RINGKEEPER_TELEGRAM_CONFIRM_MINUTES`).

```bash
docker compose --profile stt up -d --build   # faster-whisper on CPU, int8
```

- **No audio is kept.** The api downloads the voice note into memory, sends it
  to `stt`, and drops it whether transcription works or not. Neither service
  writes it to disk (`stt` runs with a read-only root filesystem) or logs it.
  Only the transcript is stored, and a pending one is cleared on Redo or
  expiry. Telegram itself keeps the voice note in your chat history, outside
  Ringkeeper.
- **Limits:** `RINGKEEPER_STT_MAX_SECONDS` (default 180) and
  `RINGKEEPER_STT_MAX_BYTES` (default 5 MB). They're checked from Telegram's
  metadata before downloading, during the download, and on the decoded audio.
  A note over a limit gets a message saying so.
- **Language: set it.** `RINGKEEPER_STT_LANGUAGE` (for example `en`) defaults
  to empty, which means detect it. Detection doubles the time (about 3 s
  instead of 1.4 s for 10 s of speech with `small`). On short or accented
  clips it can also pick the wrong language and write the transcript in that
  language.
- **Names:** the api passes your project, alias and connection names (and
  words like n8n and Supabase) to the model as a hint, so they come out
  spelled right.
- **Model:** `RINGKEEPER_STT_MODEL` (default `small`). The first start
  downloads the model from Hugging Face into the `stt-models` volume. After
  that, `RINGKEEPER_STT_OFFLINE=1` keeps the service offline. onnxruntime's
  telemetry (used for silence detection) is turned off.
- **Network:** `stt` has no published port. It's on the `internal` network,
  where only the api calls it (`RINGKEEPER_STT_URL`, default `http://stt:9000`;
  empty turns voice off).

## Install

### The desktop app (Windows 10 and 11)

Download `Ringkeeper_<version>_x64-setup.exe` from the
[latest release](https://github.com/alexyushkin/ringkeeper-releases/releases/latest)
and run it. It installs for your user only: no administrator rights, nothing
else to install, no terminal. Ringkeeper opens when it's done and walks you
through first run; you need an Anthropic API key
([platform.claude.com](https://platform.claude.com) → API keys).

**Windows may warn you first.** Until the installer is signed and well known,
Microsoft Defender SmartScreen shows **"Windows protected your PC"**. Click
**More info**, check that the app is `Ringkeeper_..._x64-setup.exe`, then
click **Run anyway**. (If your browser flags the download, choose **Keep**.)
If instead **Smart App Control** blocks it (some new Windows 11 PCs have it
on), there's no "Run anyway": use a signed release once there is one.
Turning Smart App Control off is possible in Windows Security, but it can't be
turned back on without reinstalling Windows.

- **Where things go:** the app in `%LOCALAPPDATA%\Programs\Ringkeeper`, your
  data in `%LOCALAPPDATA%\Ringkeeper` (the master key that protects your saved
  keys is in Windows Credential Manager), new projects in
  `Documents\Ringkeeper`.
- **Updates:** Ringkeeper checks when it starts and every 6 hours, downloads
  updates in the background and shows **Update ready, restart when you're
  done**. It never restarts by itself, waits for running tasks, and never
  touches your data. If an update fails, the old version keeps working.
- **Uninstall:** Settings → Apps → Ringkeeper → Uninstall. Your data stays
  unless you tick **Also delete my data**; your project folders always stay.

### The server (Docker), for advanced setups

To run Ringkeeper on a server or share it from your phone, see
**[docs/INSTALL.md](docs/INSTALL.md)**. In short, with Docker and git:

```bash
git clone https://github.com/alexyushkin/ringkeeper.git && cd ringkeeper
sh scripts/setup-env.sh       # .env with a random POSTGRES_PASSWORD; set RINGKEEPER_WORKSPACES_ROOT
docker compose --profile worker build worker
docker compose --profile stt up -d --build
docker compose exec api ringkeeper web setup-code
```

Then open http://localhost:3000 and follow the setup wizard. The guide also
covers using it from your phone, a cloud server, updates, backups and
restore, a forgotten password, and troubleshooting.

## The web app

The `web` service is a phone-first app for running Ringkeeper without a
terminal: in plain words, with **Clients & projects**, **Connections** and
**Approvals**, and technical detail behind **Show details**.

| Screen | What it's for |
| --- | --- |
| Home | Your conversations, and a box to ask for something: typed, or hold the mic to speak ("I heard: ..." first, nothing is sent until you press Send). A conversation shows how the router split it and live task cards. |
| Approvals | Everything waiting for you, in full: the SQL of a held write, a plan, a question, which project. Telegram only ever gets summaries; the full text lives here. A held write that deletes or drops data needs the connection's name typed to approve. |
| Tasks | Every task, by project and status. A task opens as a transcript: what the assistant said, each tool call (arguments and results folded away), check results, the model, steps and estimated cost, the branch, changed files and diff, and Stop and Resume. |
| Clients & projects | Create and edit projects (description, other names, folder, repository, how independently it works, checks, model overrides) and link connections, with defaults. |
| Connections | Forms for databases (Supabase, Neon, any Postgres), n8n, Airtable, Vercel, Twilio and Gmail, each with a link to where you get the key; also on each project's page, where a new connection is linked to the project. Secrets go in once, through password fields, and are never shown again. Production has a red badge. **Test connection** runs one read-only call through the adapter. |
| Activity | The audit log with filters, and a CSV export per client. |
| Settings | Claude (API key, or your subscription for personal use when `RINGKEEPER_ALLOW_SUBSCRIPTION=1`), Telegram (including the requests toggle and its privacy note), models, how many tasks at once, transcript retention, passkeys, the recovery key, and **Stop everything**. |

### First start, without a terminal

After [installing](docs/INSTALL.md), open http://localhost:3000 (or your
Tailscale address). The setup wizard asks for the **setup code** the
api printed on its first start (`docker compose exec api ringkeeper web
setup-code` shows it; whoever reaches a freshly exposed server first can't
claim it without it), then walks through:

1. your login (a password; you can add a passkey later) and your **recovery
   key**, a file to keep somewhere safe. If you lose it, you'll need to
   re-enter your connection secrets.
2. Claude: an Anthropic API key, checked with one free call before it's
   stored. With `RINGKEEPER_ALLOW_SUBSCRIPTION=1`, your Claude subscription
   instead (personal use only): a token from `claude setup-token`, or, in the
   desktop app, your Claude Code login.
3. Telegram, optional: paste the bot token, message the bot, and the wizard
   picks up your chat.
4. your first project, and 5. its first connection, with **Test connection**.

On first start the api creates the master key (`.ringkeeper/master.key`),
the API admin token and the setup code; the data dir is writable by the api
for this. Settings you change in the app are stored in the database and win
over `.env`; they apply without a restart.

### On your phone: HTTPS with Tailscale Serve

Installing the app, the microphone and passkeys all need HTTPS. Tailscale
Serve gives the app an HTTPS address only your own devices can reach; set
`RINGKEEPER_WEB_ORIGIN` to it. Step by step:
[docs/INSTALL.md, "Use it from your phone"](docs/INSTALL.md#3-use-it-from-your-phone).
A public address with your own domain, for client installs on cloud servers,
comes in a later milestone.

### How the web app is secured

- **The web app doesn't hold the admin token.** Next.js server code (the
  backend-for-frontend, `/bff`) calls the API with its own *web token*, which
  on its own reaches only setup and sign-in: everything else also needs a
  signed-in owner's session. So even a taken-over web container can't use
  the API without you. The api hands the token over in a volume only the api
  and web mount; the web container never mounts the vault or the data dir,
  and secrets go browser → web → api → vault without being logged.
  `ringkeeper web reset-login` replaces the token and signs everyone out.
- **One owner account**: an argon2id password, sessions in an httpOnly,
  `Secure`, `SameSite=Strict` cookie (only a hash is stored), ending after a
  week unused or 30 days at most. Optional passkeys (they need your
  fingerprint, face or PIN each time, and a password change removes them).
- **Guessing is slowed, never locked.** After 5 wrong passwords in 15
  minutes there's one try every 3 seconds, for everyone; a single device is
  blocked only when its address is known (`RINGKEEPER_WEB_TRUST_PROXY=1`
  behind your proxy). Nobody can lock you out, and passkey sign-in is never
  held up.
- **Sensitive changes ask for the password again** if you signed in more
  than 10 minutes ago: adding a passkey, downloading the recovery key,
  storing a secret, a connection's address or secret, taking a connection out
  of prod, Telegram chats and requests, preapproved writes and autonomy. A
  stolen session alone can't redirect approvals or take your keys.
- **Only your addresses**: the app answers on localhost, the host of
  `RINGKEEPER_WEB_ORIGIN` and `RINGKEEPER_WEB_ALLOWED_HOSTS`, so another
  website can't reach it through your browser (DNS rebinding). Requests over
  1 MB are refused (voice uploads have their own limit), and errors never
  echo what you typed.
- **Every change** must come from the app's own origin and carry the CSRF
  token (double submit). A strict Content-Security-Policy with a fresh nonce
  per page allows no inline or third-party scripts.
- **Live updates** are server-sent events from Postgres NOTIFY triggers, with
  ids only; the app fetches the rest through the API.
- **Retention**: full transcripts (session events) are deleted after
  `RINGKEEPER_RETENTION_DAYS` (default 90). Task summaries, decisions and
  the audit log stay.
- The service worker caches the app shell only, never pages or data.

## Developer quick start (CLI)

Not needed to use Ringkeeper: [docs/INSTALL.md](docs/INSTALL.md) and the setup
wizard cover installing. This is the terminal route, for developers. It needs
[uv](https://docs.astral.sh/uv/) and bash, `RINGKEEPER_DATABASE_URL` in
`.env` (uncommented, with your password), and Postgres published on this
machine with `docker-compose.dev.yml` (a normal install doesn't publish it).
Every `uv run ringkeeper ...` command also works as
`docker compose exec api ringkeeper ...`, with no uv and no published port.

```bash
sh scripts/setup-env.sh             # .env with a random POSTGRES_PASSWORD; then set RINGKEEPER_WORKSPACES_ROOT
uv run ringkeeper secret init        # creates .ringkeeper/master.key: back it up
uv run ringkeeper auth login         # the Anthropic API key workers and the router use
docker compose --profile worker build worker
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d --build
uv run ringkeeper registry apply examples/registry.yaml
curl http://localhost:8000/health
curl http://localhost:8100/health
curl -H "Authorization: Bearer $(uv run ringkeeper auth api-token)" http://localhost:8000/projects
```

The API container applies database migrations on start, and the gateway waits
for it. The example registry's workspace paths (`acme-web`, …) are relative,
so create those folders under `RINGKEEPER_WORKSPACES_ROOT`.

### Demo: connect Claude Code to the gateway

The `demo` profile adds a Postgres that stands in for the clients' Supabase
projects:

```bash
docker compose --profile demo up -d --build
for db in acme_dev:acme/supabase-dev acme_prod:acme/supabase-prod globex_dev:globex/supabase-dev; do
  printf '%s' "postgresql://demo:demo-local-only@demo-targets:5432/${db%%:*}" \
    | uv run ringkeeper secret set "file://${db#*:}"
done
uv run ringkeeper registry apply examples/registry.yaml
claude mcp add --transport http --scope user ringkeeper-database \
  http://localhost:8100/mcp/database \
  --header "Authorization: Bearer $(uv run ringkeeper token issue --project acme-web --ttl 1h)"
```

MCP servers load when a Claude Code session starts, so start a new session
after adding it. Use `--scope user` for the Claude desktop app, which doesn't
load `--scope local` servers (the terminal CLI loads either). User scope makes
the server visible in every project, so remove it when you're done:
`claude mcp remove ringkeeper-database -s user`.

Then, from Claude Code:

- **A read on an acme instance works:** `query` with
  `SELECT * FROM customers` uses acme-web's default, `acme-supabase-dev`.
- **A globex instance is refused:** `query` with
  `instance: globex-supabase-dev` returns "not available to project
  'acme-web'".
- **A prod write is held:** `execute` on `acme-supabase-prod` returns "Held
  for approval" and a decision id.

Approve the held write and check the audit log:

```bash
docker compose exec gateway ringkeeper decision approve <id>
uv run ringkeeper audit list
```

## CLI

```
ringkeeper project  add | list | show | remove
ringkeeper instance add | list | show | remove [--force]
ringkeeper bind <project> <instance> [--default] [--tools a,b] [--preapprove x,y]
ringkeeper unbind <project> <instance>
ringkeeper registry apply <file.yaml> [--dry-run]
ringkeeper registry export [-o file.yaml]
ringkeeper secret   init | set <ref> | check <instance>
ringkeeper token    issue --project <name> [--ttl 1h] [--session <id>] | revoke <jti>
ringkeeper decision list | show | approve | deny
ringkeeper audit    list [--project] [--instance] [--outcome] [--since]
ringkeeper auth     login | status | api-token [--rotate]
ringkeeper ask      "<message>" [--thread <id>] [--no-wait]
ringkeeper inbox    list [--kind] | show <id> | answer <id> <reply> [--note]
ringkeeper routing  examples list | examples delete <id>
ringkeeper telegram login | chats | test
ringkeeper web      setup-code | reset-login [--yes]
ringkeeper task     run --project <name> "<prompt>" [--max-turns] [--max-runtime] [--budget] [--detach]
ringkeeper task     list | show <id> | stop <id> | resume <id> "<follow-up>"
ringkeeper stop-all
ringkeeper db upgrade
```

- `bind` sets the binding to exactly the flags given, so running it again on
  an existing binding updates it.
- `registry apply` creates and updates, and never deletes anything missing
  from the file.

## API

Every endpoint except `/health` needs the admin token, reads included, because
project names, decisions and audit rows are client data. (The web app's own
token works only with a signed-in owner's session.)
`ringkeeper auth api-token` prints it, creating a random one in the vault on
first use; `--rotate` replaces it, and the old one stops working at once. The
OpenAPI schema and docs pages are off.

| Endpoint                              | Does                                         |
| ------------------------------------- | -------------------------------------------- |
| `GET /health`                         | `{"status": "ok", "database": "ok"}` (open)  |
| `GET /projects`, `/projects/{name}`   | projects, one with its bound instances       |
| `GET /instances`                      | all instances                                |
| `POST /messages`                      | queue a message for the router (`202`)       |
| `GET /threads/{id}`                   | a thread's messages and routing results      |
| `GET /decisions`, `/decisions/{id}`   | the inbox (`?status=`, `?kind=`), in full    |
| `POST /decisions/{id}/answer`         | `{"choice": ..., "text": ..., "by": ...}`; `confirm` (the connection's name) for a write that deletes data |

The web app's endpoints need a token **and** a signed-in owner
(`X-Ringkeeper-Session`, which the web app's server adds). Writes are
audited as `changed`, and secret endpoints are write-only. Sensitive ones
answer `403` with `X-Ringkeeper-Confirm: password` unless the session signed
in or confirmed (`POST /auth/confirm`) in the last 10 minutes:

| Area | Endpoints |
| --- | --- |
| Setup and sign-in | `GET /setup/status`, `POST /setup/owner` (with the setup code), `POST /setup/recovery-key`, `POST /auth/login`, `/auth/confirm`, `/auth/logout`, `GET /auth/session`, `POST /auth/password`, passkeys under `/auth/passkeys` |
| Settings | `GET`/`PATCH /settings`, `POST /stop-all`, `POST /resume-all` |
| Secrets (write-only) | `PUT /secrets/claude`, `PUT /secrets/telegram`, `PUT /connections/{name}/secret`; `GET /telegram/detect?code=` (only the chat that sent the app's pairing code) |
| Projects | `POST /projects`, `PATCH`/`DELETE /projects/{name}`, `PUT`/`DELETE /projects/{name}/connections/{connection}` |
| Connections | `GET`/`POST /connections`, `PATCH`/`DELETE /connections/{name}`, `POST /connections/{name}/test` |
| Work | `GET /threads`, `/threads/{id}/tasks`, `/tasks`, `/tasks/{id}`, `/tasks/{id}/events`; `POST /tasks/{id}/stop`, `/tasks/{id}/resume` |
| Activity | `GET /audit`, `GET /audit.csv` |
| Voice and live | `POST /voice/transcribe` (audio in, transcript out, nothing kept), `GET /events` (server-sent events) |

```bash
TOKEN=$(uv run ringkeeper auth api-token)
curl -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"body": "How many customers has globex?"}' http://localhost:8000/messages
```

The API never returns `secret_ref`.

## Development

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest            # starts a throwaway Postgres 16 with testcontainers
RINGKEEPER_TEST_DB=sqlite uv run pytest   # the same suite on SQLite (desktop mode)
```

Ringkeeper's own database is Postgres in server mode and SQLite in desktop
mode. Services use the ORM; anything database-specific (column types, upserts,
locks, NOTIFY/LISTEN) lives in `src/ringkeeper/storage/`, and a test fails if
it shows up anywhere else. Migrations from 0009 on must run on both (`ringkeeper
db upgrade` against a `sqlite:///` URL to try one); 0001–0008 are Postgres-only,
and SQLite gets their result in one step (`storage/sqlite_baseline.py`).

### Desktop mode from source

The desktop app's backend is `ringkeeper desktop`: SQLite, the master key in
the OS keychain, workers as local processes (no Docker), API and gateway on
random 127.0.0.1 ports. It writes `desktop.json` (the address and a
per-launch secret) to its data folder for the app shell.

```bash
cd web && node scripts/build-desktop.mjs && cd ..   # the UI as static files
uv run ringkeeper desktop --web-dir web/.desktop/out --data-dir ./.desktop-data
```

Then open the window the way the shell does: POST `/desktop/codes` with
`Authorization: Bearer <secret>` and open `/desktop/start?code=<code>`. There
is no sign-in; that link is the lock (see `src/ringkeeper/desktop/server.py`).

The app itself is the shell in `shell/` (Tauri 2, Rust): the window, the tray,
and the backend it starts, restarts and stops. A debug build runs the backend
from this repository's `.venv`:

```bash
cd shell && cargo run            # needs Rust (rustup) and, on Windows, the MSVC build tools
cargo run -- --hidden            # tray only, as when it starts at sign-in
cargo run -- --quit              # asks the running app to quit
```

It keeps its data in the per-user app folder (`%LOCALAPPDATA%\Ringkeeper` on
Windows); set `RINGKEEPER_SHELL_DATA_DIR` to use another one. Its own log is
`logs/shell.log` there, next to the backend's `logs/backend.log`.

To build the Windows installer locally (what the release workflow does on a
tag, minus signing and publishing):

```bash
uv run python shell/scripts/bundle.py   # Python + packages, web app, MinGit, notices -> shell/resources
cd shell && npx @tauri-apps/cli@2.12.2 build --config bundle/windows.json
# -> shell/target/release/bundle/nsis/Ringkeeper_<version>_x64-setup.exe
```

Releases, signing and the release checklist: [docs/RELEASING.md](docs/RELEASING.md),
[docs/SIGNING.md](docs/SIGNING.md), [docs/RELEASE_CHECK.md](docs/RELEASE_CHECK.md).

Adding a service means one module in `src/ringkeeper/adapters/` (tools declared
`@read` or `@write`), one entry in `adapters/__init__.py`, and tests. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
