# Oracle Database

Your own Oracle Database, or an Autonomous Database with its wallet. Nothing else needs
installing: Ringkeeper connects in thin mode, with no Oracle client.

## Add it

Use the **Oracle Database** tile under Connectors, with a user that has only the access it
needs. The connect string looks like `db.example.com:1521/orclpdb1`.

For an **Autonomous Database**, download the wallet `.zip` from its Database connection page,
choose it in the form, and use one of its service names as the connect string, like
`acme_high`. Ringkeeper uses only `ewallet.pem` and `tnsnames.ora` from it. The wallet is kept
in the vault and unpacked only for the length of a call.

If a project's `.env` has `ORACLE_DSN`, `ORACLE_USER` and `ORACLE_PASSWORD`, Ringkeeper
offers it on the project's page.

## What's held

The same checks as a Postgres database:

- **Reading runs,** in a read-only transaction.
- **On production,** every change waits for your approval. On test data, changes run and
  are logged.
- **Needs the name typed:** deleting rows, dropping or changing tables, permissions, and
  anything that can't be checked, including PL/SQL blocks and calls to your own functions.

A plain `UPDATE` or `DELETE` on one table with a primary key can be undone for 7 days.

## Good to know

- An Oracle function can write even inside a read-only transaction, so a query that calls
  a function Ringkeeper doesn't know has to go through `execute`, where it's checked.
- Oracle's `q'[...]'` strings can hide what a statement does, so they count as something
  that can't be checked.
- Values use `:1`, `:2` binds.
