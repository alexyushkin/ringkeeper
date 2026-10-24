# Interlock

Interlock is a self-hosted orchestrator that routes tasks to projects and runs
coding-agent workers against them. It is MIT licensed and early: this first
milestone ships the registries only (projects, instances, and the bindings
between them), plus a CLI and a read-only API.

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
                   └── acme-n8n             (default n8n)

project globex-portal ── globex-supabase-dev (default supabase)
```

A worker running for `acme-web` can only ever be handed `acme-*` instances.
The `globex-*` instances don't exist from its point of view.

A few rules keep this safe:

- **Secrets never live in Interlock.** An instance stores a `secret_ref`, a
  pointer such as `file:///run/secrets/acme-supabase-dev` or
  `aws-sm://interlock/acme/supabase-dev`. Anything else is rejected.
- **One default per type.** A project has at most one default instance per
  service type, so "use Supabase" is never ambiguous.
- **Prod is never a default.** A prod instance can be bound, but a write to it
  must name the instance explicitly.
- **Bound instances are protected.** Removing an instance that a project still
  uses fails unless you pass `--force`.

Bindings can also narrow the tools a worker may use on an instance
(`allowed_tools`, empty means all) and list writes that don't need a human
approval (`preapproved_writes`).

## Quick start

Requirements: Docker with Compose, and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
docker compose up -d --build
curl http://localhost:8000/health
```

The API container applies database migrations on start. Then load the example
registry from the host:

```bash
uv run interlock registry apply examples/registry.yaml --dry-run
uv run interlock registry apply examples/registry.yaml
uv run interlock project show acme-web
```

Running `registry apply` again reports no changes.

## CLI

```
interlock project  add | list | show | remove
interlock instance add | list | show | remove [--force]
interlock bind <project> <instance> [--default] [--tools a,b] [--preapprove x,y]
interlock unbind <project> <instance>
interlock registry apply <file.yaml> [--dry-run]
interlock registry export [-o file.yaml]
interlock db upgrade
```

`bind` sets the binding to exactly the flags given, so running it again on an
existing binding updates it. `registry apply` creates and updates, and never
deletes anything missing from the file.

## API (read-only)

| Endpoint                | Returns                                         |
| ----------------------- | ----------------------------------------------- |
| `GET /health`           | `{"status": "ok", "database": "ok"}`             |
| `GET /projects`         | all projects                                    |
| `GET /projects/{name}`  | one project with its bound instances            |
| `GET /instances`        | all instances                                   |

The API never returns `secret_ref`.

## Development

```bash
uv sync
uv run pre-commit install
uv run ruff check . && uv run ruff format --check .
uv run pyright
uv run pytest            # starts a throwaway Postgres 16 with testcontainers
```

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
