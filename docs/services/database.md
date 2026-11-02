# Databases (Supabase, Neon, any Postgres)

## Where to get the key

The **connection string**, starting with `postgresql://`:

- **Supabase:** open the [project](https://supabase.com/dashboard/projects), click
  **Connect**, and copy the **Session pooler** string.
- **Neon:** open the [project](https://console.neon.tech/app/projects), click
  **Connect**, and copy the connection string.
- **Any other Postgres** reachable from your computer: its usual connection string.

Ringkeeper won't connect to an address on your own computer, or to itself.
A database user that can only read is a good choice for a production database
you only want reports from.

## What it can do

- **Read:** list tables, describe a table, run a query. Every query runs inside a
  read-only transaction, one statement at a time.
- **Change:** run one statement that writes (`INSERT`, `UPDATE`, `DELETE`, schema
  changes). A statement that only reads runs at once, even through this tool.

## What's held

Reads always run. Changes run straight away on a staging or development connection, and wait for your approval on a production one. Deleting rows, dropping or altering tables, and anything the check can't
read for certain need the connection's name typed to approve.

## Undo

A plain `UPDATE` or `DELETE` on one table with a primary key can be reverted
for 7 days ([Undo](../undo.md)). An `INSERT` and schema changes can't.
