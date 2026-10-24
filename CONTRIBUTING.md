# Contributing to Ringkeeper

Thanks for your interest! The project is at an early stage, so this guide is
short and will grow.

## Setup

1. Install [uv](https://docs.astral.sh/uv/) and Docker (the tests start a real
   Postgres with testcontainers).
2. `uv sync`
3. `uv run pre-commit install` (runs ruff and gitleaks on each commit)

## Before opening a pull request

```bash
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pytest
```

CI runs the same checks plus a gitleaks scan of the full history.

## Ground rules

- **No secrets, ever.** Not in code, tests, fixtures or examples. Instances
  hold a `secret_ref` pointer only. Use fictional names (`acme`, `globex`)
  and `.example` domains in examples and tests.
- **Rules live in the services layer** (`src/ringkeeper/services`). The CLI,
  API and gateway call services; they never write to the database directly.
  Every gateway call and approval goes through `services/calls.py`.
- **Secret values never leave the process.** Resolve them through the vault,
  use `Secret.reveal()` only at the point of use, and never put one in an
  exception message, log call or return value. `tests/test_no_leaks.py` must
  stay green; extend it when you add a new output path.
- **Adapters** live in `src/ringkeeper/adapters/`, one module per service. Every
  tool is declared `@read`, `@write` or `@guarded_write(precondition)` and gets
  tests against a real or faked service.
- **The worker** (`worker/ringkeeper_worker`) is its own package and image: it
  must never import `ringkeeper`, and nothing may add the master key, the data
  dir or the Docker socket to it. `tests/test_worker_container.py` checks this
  against real containers.
- **Schema changes need an Alembic migration** in
  `src/ringkeeper/migrations/versions`, and `tests/test_migrations.py` must stay
  green.
- Keep pyright strict. If a library's types get in the way, use a targeted
  `# pyright: ignore[rule]` on the line, not a global relaxation.
- Small, focused commits with clear messages.
- Never commit `.ringkeeper/` (the vault and master key) or a real `.env`.
