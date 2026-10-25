"""The database seams between server mode (Postgres) and desktop mode (SQLite).

Services use the ORM for nearly everything, and that is portable. What isn't
lives here, and nowhere else imports `sqlalchemy.dialects.postgresql`:

- `types`: column types that are ARRAY/JSONB/timestamptz on Postgres and
  JSON/text on SQLite;
- `sql`: engines (SQLite pragmas, one writer at a time), upserts, locks;
- `events`: NOTIFY/LISTEN on Postgres, an in-process bus on SQLite.
"""
