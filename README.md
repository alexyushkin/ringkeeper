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

## Quick start

Requirements: Docker with Compose, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env                # then set POSTGRES_PASSWORD and RINGKEEPER_WORKSPACES_ROOT
uv run ringkeeper secret init        # creates .ringkeeper/master.key: back it up
uv run ringkeeper auth login         # the Anthropic API key workers use
docker compose --profile worker build worker
docker compose up -d --build        # postgres, api (with the session manager), gateway
uv run ringkeeper registry apply examples/registry.yaml
curl http://localhost:8000/health
curl http://localhost:8100/health
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
ringkeeper auth     login | status
ringkeeper task     run --project <name> "<prompt>" [--max-turns] [--max-runtime] [--budget] [--detach]
ringkeeper task     list | show <id> | stop <id> | resume <id> "<follow-up>"
ringkeeper stop-all
ringkeeper db upgrade
```

- `bind` sets the binding to exactly the flags given, so running it again on
  an existing binding updates it.
- `registry apply` creates and updates, and never deletes anything missing
  from the file.

## API (read-only)

| Endpoint                | Returns                                   |
| ----------------------- | ----------------------------------------- |
| `GET /health`           | `{"status": "ok", "database": "ok"}`      |
| `GET /projects`         | all projects                              |
| `GET /projects/{name}`  | one project with its bound instances      |
| `GET /instances`        | all instances                             |

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
