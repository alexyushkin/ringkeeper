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
(per account, in its settings), except sending email.

An email to more than 10 people at once needs the connection's name typed, on every email
connection, and can't be approved from Telegram, Slack or Discord.

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

The same goes for what can't be taken back on the newer connections: sending to many
people at once, deleting a file or an email, a DELETE through an API. These always ask,
even on test data; they can't be trusted for an hour, and no per-tool setting lets them
run by themselves.

**Business systems follow the action, not the vendor.** Reports and lookups run. Drafts and
edits wait for approval, even on test data. Anything that sends to real people, publishes,
posts to the books, moves money, deletes, changes who has access or adds ongoing cost needs
the name typed, in the app itself. On these services a per-tool setting can make a tool
stricter, never looser. Nothing on an accounting connection ever runs by itself, even after
you've approved one like it. An approval that moves money shows the amount, currency,
counterparty and account in large text, as written in the call; anything the call doesn't
say shows as not stated.

**Card and bank numbers never go through.** A tool call that carries one is refused
before it's sent, and they're removed from logs, conversations, a task's events and the
audit log. Use the service's own hosted page, or a token it issued.

A held action expires after 24 hours if nobody answers.

## Snooze

**Not now?** under an approval snoozes it for an hour, three hours, or until tomorrow at 9
(only the times before it expires are offered). Until then it isn't in Approvals or its count,
and nothing notifies you about it. Then it's back, with a fresh notification in the desktop app
and your channels. Snoozed approvals are listed at the bottom of Approvals; **Wake it now**
brings one back early. Snoozing is in Activity. The task that's waiting goes on waiting: a snooze
doesn't approve or deny anything.

J, K, A and D work on the Approvals page, and one action can be trusted for an hour:
see [Working faster](working-faster.md#approvals-by-keyboard). On a business system or an
add-on tool, that covers that one action on that connection, not everything it can do.

## The record

Every change attempt and every refusal is in **Activity** with the time,
project, connection, what was asked, and what happened. Nothing in that log can
be edited or deleted, by you or by Ringkeeper.

## Stop everything

**Settings › Stop everything** stops every running task and pauses new work.
Approvals wait where they are until you resume.
