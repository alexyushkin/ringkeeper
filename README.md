# Interlock

Interlock is a self-hosted orchestrator that routes tasks to projects and runs
coding-agent workers against them. It is MIT licensed and early. So far it
ships the registries (projects, instances and the bindings between them), a
vault, an MCP gateway that enforces the bindings, held approvals for prod
writes, and an append-only audit log. There is no session manager, router or
UI yet.

## The idea: instances bound to projects

If you work for several clients you probably have several accounts of the same
service: three Supabase projects, two n8n hosts, a couple of Vercel teams. An
agent that can reach "Supabase" can reach all of them, and one wrong project
ref is all it takes to write to the wrong client's database.

Interlock models each account as an **instance** and binds instances to
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
Interlock has one 32-byte master key. It derives two keys from it: the key
that encrypts the `file://` vault, and the key that signs worker tokens.
Without the master key the vault can't be decrypted, and every outstanding
token stops verifying. There is no recovery: you would have to re-enter every
secret and issue new tokens.

The key lives in `INTERLOCK_MASTER_KEY` if that is set. Otherwise it lives in
`<data dir>/master.key`, which the CLI creates on first use. The data dir is
`./.interlock` by default and holds `vault.json` too.

**Back it up** like any other root credential:

- Copy `.interlock/master.key` (or your `INTERLOCK_MASTER_KEY` value) into a
  password manager or a secrets store you already trust. A backup of
  `vault.json` is useless without it.
- Back up `.interlock/vault.json` whenever you change secrets. It is encrypted,
  so it can go with your normal backups, as long as the key isn't stored
  beside it.
- To restore, put both files back in the data dir, or set
  `INTERLOCK_MASTER_KEY` and restore only `vault.json`.

**Workers must never receive the master key or the data dir.** Only the
gateway and the CLI read them. The compose file mounts `.interlock` into the
gateway read-only and into nothing else. Don't mount it into a worker's
container or workspace, and don't put `INTERLOCK_MASTER_KEY` in a worker's
environment. A worker gets exactly one thing: a short-lived token for its
project. The gateway refuses to start without an existing key, so it can
never silently mint a new one and orphan the vault.

```bash
printf '%s' 'postgresql://…' | uv run interlock secret set file://acme/supabase-dev
uv run interlock secret set aws-sm://interlock/acme/n8n   # hidden prompt
uv run interlock secret check acme-supabase-dev           # never prints the value
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
uv run interlock token issue --project acme-web --ttl 1h
```

This prints a signed token that names the project, a session id and an
expiry. The gateway trusts nothing else about the caller. Tokens are verified
on every request, so an expired token stops working mid-session.

### Decisions and the audit log

```bash
uv run interlock decision list                 # pending by default
uv run interlock decision show <id>            # exact args it would run
uv run interlock decision approve <id>         # re-check, run, record result
uv run interlock decision deny <id> --reason "…"
uv run interlock audit list --project acme-web --outcome held --since 1d
```

`approve` runs the stored call from the process you run it in, so it needs
network access to the instance. For the compose demo, run it inside the
gateway container (shown below).

The audit log gets a row for every write attempt and every refusal. Each row
holds the time, project, session, instance, tool, outcome (`refused`, `held`,
`executed` or `failed`), redacted args with their hash, and a truncated
result. The database itself rejects any `UPDATE` or `DELETE` on it.

## Quick start

Requirements: Docker with Compose, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
uv run interlock secret init        # creates .interlock/master.key: back it up
docker compose up -d --build        # postgres, api, gateway
uv run interlock registry apply examples/registry.yaml
curl http://localhost:8000/health
curl http://localhost:8100/health
```

The API container applies database migrations on start, and the gateway waits
for it.

### Demo: connect Claude Code to the gateway

The `demo` profile adds a Postgres that stands in for the clients' Supabase
projects:

```bash
docker compose --profile demo up -d --build
for db in acme_dev:acme/supabase-dev acme_prod:acme/supabase-prod globex_dev:globex/supabase-dev; do
  printf '%s' "postgresql://demo:demo-local-only@demo-targets:5432/${db%%:*}" \
    | uv run interlock secret set "file://${db#*:}"
done
uv run interlock registry apply examples/registry.yaml
claude mcp add --transport http --scope local interlock-supabase \
  http://localhost:8100/mcp/supabase \
  --header "Authorization: Bearer $(uv run interlock token issue --project acme-web --ttl 1h)"
```

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
docker compose exec gateway interlock decision approve <id>
uv run interlock audit list
```

## CLI

```
interlock project  add | list | show | remove
interlock instance add | list | show | remove [--force]
interlock bind <project> <instance> [--default] [--tools a,b] [--preapprove x,y]
interlock unbind <project> <instance>
interlock registry apply <file.yaml> [--dry-run]
interlock registry export [-o file.yaml]
interlock secret   init | set <ref> | check <instance>
interlock token    issue --project <name> [--ttl 1h] [--session <id>]
interlock decision list | show | approve | deny
interlock audit    list [--project] [--instance] [--outcome] [--since]
interlock db upgrade
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

Adding a service means one module in `src/interlock/adapters/` (tools declared
`@read` or `@write`), one entry in `adapters/__init__.py`, and tests. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
