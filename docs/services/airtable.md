# Airtable

## Where to get the key

A **personal access token** from [Airtable](https://airtable.com/create/tokens):
add the `data.records:read` and `schema.bases:read` scopes (and
`data.records:write` if Ringkeeper may change records), and add **only this
client's bases**. It starts with `pat`.

## What it can do

- **Read:** list bases, tables (with their fields) and records; get one record.
- **Change:** create, update or delete up to 10 records at a time.

## What's held

Reads always run. Changes run straight away on a staging or development connection, and wait for your approval on a production one. Changing or deleting records needs the connection's name typed to approve; adding
records doesn't.

## Narrowing it

On the client's page, a project's link can be limited to **one base** (its ID
starts with `app`): calls from that project can't reach the client's other bases.

## Undo

All three changes can be reverted for 7 days ([Undo](../undo.md)). Deleted
records come back as new records, with new IDs.
