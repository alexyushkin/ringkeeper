# Ringkeeper

Ringkeeper is a self-hosted orchestrator that routes tasks to projects and runs
coding-agent workers against them. It is MIT licensed and early. So far it
ships:
- the registries: projects, instances and the bindings between them
- a vault for credentials
- an MCP gateway that enforces the bindings
- held approvals for prod writes, and an append-only audit log
- a session manager that runs Claude Agent SDK workers in containers

There is no router, inbox UI or architect yet.

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

## How a call flows

```
worker (Claude Code, …)
   │  MCP over HTTP, Authorization: Bearer <worker token>
   ▼
gateway  /mcp/supabase  /mcp/n8n
   1. valid token?            (signed, short-lived, names project + session)
   2. instance exists?        (omitted only for reads: the project default)
   3. bound to the project?
   4. tool in allowed_tools?  (empty = all)
   ├─ read                  → run
   ├─ write, dev/staging    → run, audit
   └─ write, prod           → hold as a decision, audit
                               unless the tool is in preapproved_writes
   ▼
adapter (supabase, n8n)  ← secret resolved from the vault, never returned
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
- **Bound instances are protected.** Removing an instance that a project still
  uses fails unless you pass `--force`.

## The vault and the master key

**Losing `master.key` means losing the vault and every worker token.**
Ringkeeper has one 32-byte master key. It derives two keys from it: the key
that encrypts the `file://` vault, and the key that signs worker tokens.
Without the master key the vault can't be decrypted, and every outstanding
token stops verifying. There is no recovery: you would have to re-enter every
secret and issue new tokens.

The key lives in `RINGKEEPER_MASTER_KEY` if that is set. Otherwise it lives in
`<data dir>/master.key`, which the CLI creates on first use. The data dir is
`./.ringkeeper` by default and holds `vault.json` too.

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
| `/mcp/supabase` | `list_tables`, `describe_table`, `query` (READ ONLY transaction) | `execute` |
| `/mcp/n8n` | `list_workflows`, `get_workflow`, `list_executions`, `get_execution` | `activate_workflow`, `deactivate_workflow`, `update_workflow` |

- **supabase:** the secret is a Postgres connection string. Each call runs
  exactly one statement over the extended protocol, so `COMMIT; …` tricks
  can't escape a read-only transaction.
- **n8n:** the secret is the API key and `base_url` is the host.
  `update_workflow` never puts a live workflow's new version live unless you
  pass `republish=true`. A held update is refused at approval if the workflow
  changed after the hold (its `updatedAt` moved), so an old approval can't
  overwrite newer edits.

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
  same Claude session
- a gateway token scoped to the project, valid for the task's max runtime and
  revoked when the session ends
- exactly one Claude credential: `ANTHROPIC_API_KEY` in `api_key` mode. Its
  environment is built from an allowlist, so nothing that could switch billing
  (`ANTHROPIC_AUTH_TOKEN`, `CLAUDE_CODE_USE_BEDROCK`, `ANTHROPIC_BASE_URL`, …)
  is ever inherited.

It never gets the master key, the data dir or the Docker socket. It runs as a
non-root user on a read-only root filesystem, with all capabilities dropped and
memory and process limits. It is on a network that holds only the gateway, so
it can't reach Postgres or the api by name. Settings files (user, project,
local) aren't loaded, and the project's `autonomy` removes tools: `strict` has
no Bash and no web access, `standard` has no web access.

**Git:** if the workspace is a git repo, the worker commits on
`ringkeeper/<task-id>` and never moves `main`. If the agent moves `main`
anyway, it's put back and the moved commits are kept on a side branch. Only
files the worker touched are committed, never your own uncommitted edits.
Nothing is pushed. Without a repo, the task records the list of changed files.

**The session manager** lives in the api service. It is the only service that
mounts the Docker socket, which makes it root-equivalent on the host: guard
the api like root. Build the worker image once with
`docker compose --profile worker build worker`.

### Known risks (not solved yet)

- **The worker holds the Claude credential in its environment.** A
  prompt-injected worker with Bash could read it and send it out. Egress
  allowlisting comes later; until then, prefer `strict` autonomy (no Bash)
  for anything that reads untrusted content.
- **Workers have open internet access**, which they need for the Anthropic
  API.
- **Host-published ports are reachable by IP.** On Docker Desktop, the
  host-gateway address forwards to the host's loopback, so a worker that knows
  that address could reach anything published there, including Postgres.
  The host name `host.docker.internal` is blocked, but the IP isn't. Set a
  strong `POSTGRES_PASSWORD` in `.env`.

### Subscription mode: personal use only

`api_key` is the default and the only mode setup offers. Anthropic's Agent
SDK terms don't allow offering claude.ai (subscription) login in products
built on the SDK without Anthropic's approval. Subscription mode therefore exists only for running
Ringkeeper yourself on **your own** Claude account. Never use it to run work
for anyone else.

If that's your case, set `RINGKEEPER_CLAUDE_AUTH_MODE=subscription` in `.env`
and store a token from `claude setup-token` with
`ringkeeper auth login --subscription`. Workers then get
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

A `feature` task starts with an **architect** session, not a worker. It runs
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
| architect                     | `claude-opus-5-5`   | `RINGKEEPER_MODEL_ARCHITECT`  |
| worker (no complexity)        | `claude-sonnet-5-5` | `RINGKEEPER_MODEL_WORKER`     |
| worker, `low` complexity      | `claude-haiku-5-5`  | `RINGKEEPER_MODEL_LOW`        |
| worker, `medium` complexity   | `claude-sonnet-5-5` | `RINGKEEPER_MODEL_MEDIUM`     |
| worker, `high` complexity     | `claude-opus-5-5`   | `RINGKEEPER_MODEL_HIGH`       |

The router sets each task's complexity. After two failed check rounds in the
architect loop, the worker moves up one tier. A project can override any of
these in the registry (`models: {architect: ..., low: ...}`), and each session
records the model it ran on (`ringkeeper task show`).

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

```bash
uv run ringkeeper telegram login          # the bot token from @BotFather (hidden prompt)
# message the bot from your phone, then:
uv run ringkeeper telegram chats          # find your chat id
# .env: RINGKEEPER_TELEGRAM=1 and RINGKEEPER_TELEGRAM_CHAT_IDS='[123456789]'
uv run ringkeeper telegram test
docker compose up -d                      # the api service runs the bridge (long polling)
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

## Quick start

Requirements: Docker with Compose, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                # then set POSTGRES_PASSWORD and RINGKEEPER_WORKSPACES_ROOT
uv run ringkeeper secret init        # creates .ringkeeper/master.key: back it up
uv run ringkeeper auth login         # the Anthropic API key workers and the router use
docker compose --profile worker build worker
docker compose up -d --build        # postgres, api (session manager, router), gateway
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
claude mcp add --transport http --scope user ringkeeper-supabase \
  http://localhost:8100/mcp/supabase \
  --header "Authorization: Bearer $(uv run ringkeeper token issue --project acme-web --ttl 1h)"
```

MCP servers load when a Claude Code session starts, so start a new session
after adding it. Use `--scope user` for the Claude desktop app, which doesn't
load `--scope local` servers (the terminal CLI loads either). User scope makes
the server visible in every project, so remove it when you're done:
`claude mcp remove ringkeeper-supabase -s user`.

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
project names, decisions and audit rows are client data.
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
| `POST /decisions/{id}/answer`         | `{"choice": ..., "text": ..., "by": ...}`    |

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
```

Adding a service means one module in `src/ringkeeper/adapters/` (tools declared
`@read` or `@write`), one entry in `adapters/__init__.py`, and tests. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
