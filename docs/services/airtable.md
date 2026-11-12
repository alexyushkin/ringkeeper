# Airtable

## Where to get the key

A **personal access token** from [Airtable](https://airtable.com/create/tokens):
add the `data.records:read` and `schema.bases:read` scopes (and
`data.records:write` if Ringkeeper may change records), and add **only this
client's bases**. It starts with `pat`.

## What it can do

- **Read:** list bases, tables (with their fields) and records; get one record. Each list
  says how many there are in all (records: counted up to 2,000), not only on the page it shows.
- **Change:** create, update or delete up to 10 records at a time.
- **Not here:** automations and interfaces. For those, add **Airtable's own server** too (in
  the connectors, next to the built-in one, or **Add Airtable's own server for automations** on
  this connection's card): you sign in on Airtable's page, and the same base can be pinned.

## Both on one project

A project can have the built-in connection and Airtable's own server. Its tasks are told
which is for what: records, bases and tables through the built-in one (with undo),
automations and interfaces through Airtable's own server. When a task ends not done because
the built-in one can't see automations, and the client hasn't added Airtable's own server,
the result says so, with a link to add it. Check my setup and the server's **Test** say
whether its tools include automations.

## What's held

Reads always run. Changes run straight away on a staging or development connection, and wait for your approval on a production one. Changing or deleting records needs the connection's name typed to approve; adding
records doesn't.

## Narrowing it

On the client's page, a project's link can be limited to **one base** (its ID
starts with `app`): calls from that project can't reach the client's other bases.

## Undo

All three changes can be reverted for 7 days ([Undo](../undo.md)). Deleted
records come back as new records, with new IDs.
