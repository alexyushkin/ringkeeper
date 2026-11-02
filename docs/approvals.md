# Approvals and safety

Ringkeeper lets the AI read freely and makes it ask before it changes anything
that matters.

## What waits for you

- **A change on a production connection**: a database update, a new Airtable
  record, an n8n workflow change, a Vercel deploy.
- **Anything that reaches real people**, on any connection: an email, a text, a
  call, a calendar invite, a shared file.

Reads never wait. Changes on staging and development connections run straight
away and are logged. A Google account can let a single tool run without asking
(per account, in its settings).

## Approving

Held actions are in **Approvals** (with a count in the sidebar), in their
conversation, and in your notification channels. Each shows exactly what would
run, on which connection, for which client and project. **Approve** runs it;
**Deny** (with an optional note) tells the AI not to, and why.

Before it runs, an approved action is checked again against today's setup: if
the connection was unlinked or narrowed since, it's refused. Where it can,
Ringkeeper also checks the target hasn't changed since the action was held, and
asks the AI to redo it if it has.

**Destructive changes need the connection's name typed**: deleting rows,
dropping or altering tables, anything the check can't read for certain. These
can't be approved from Telegram, Slack or Discord: those say *Open in
Ringkeeper*.

A held action expires after 24 hours if nobody answers.

## The record

Every change attempt and every refusal is in **Activity** with the time,
project, connection, what was asked, and what happened. Nothing in that log can
be edited or deleted, by you or by Ringkeeper.

## Stop everything

**Settings › Stop everything** stops every running task and pauses new work.
Approvals wait where they are until you resume.
