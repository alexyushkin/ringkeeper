# Contributing to Interlock

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
- **Rules live in the services layer** (`src/interlock/services`). The CLI and
  API call services; they never write to the database directly.
- **Schema changes need an Alembic migration** in
  `src/interlock/migrations/versions`, and `tests/test_migrations.py` must stay
  green.
- Keep pyright strict. If a library's types get in the way, use a targeted
  `# pyright: ignore[rule]` on the line, not a global relaxation.
- Small, focused commits with clear messages.
