# Undo

When a change runs on a connection (because you approved it, or because it runs
without asking on a staging or development connection), Ringkeeper first keeps a
copy of what it's about to change. For 7 days you can put it back.

## What can be undone

- **Databases:** a plain `UPDATE` or `DELETE` on one table that has a primary
  key. Ringkeeper keeps only the rows it touches (for an update, only the
  columns it sets). Reverting puts the old values back, or the deleted rows.
- **Airtable:** changed fields go back to what they were; deleted records come
  back (as new records, with new IDs); added records are deleted.
- **n8n:** a replaced workflow goes back to how it was; a workflow turned on or
  off goes back.

## What can't

An email or text that was sent, a call, a deploy, a push to GitHub, an `INSERT`
(Ringkeeper can't tell which rows it added), changes to tables themselves, and
anything another tool's server does. The approval card says **Can't be
undone** and why, before you approve.

## Reverting

Open the task (or its conversation): **Data it changed** lists each one with
**Revert** and what it will put back. Reverting is itself a change, so it waits
for your OK in Approvals. If something else changed those rows or records since,
the approval says so; and if they change between asking and approving, the
revert stops instead of overwriting them.

## Space and time

Kept changes live in Ringkeeper's data folder and are deleted after 7 days.
Each project has 50 MB for them; past that, a change still runs but isn't kept,
and its task says so. Space frees up as old ones pass 7 days.
