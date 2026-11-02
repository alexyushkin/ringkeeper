# How I built Ringkeeper

*A draft write-up, about 800 words, to adapt for LinkedIn or a blog.*

I build automations for clients, and each one has its own database, its own
Airtable, its own n8n, its own Vercel team and its own inbox. AI agents are now good enough to do a lot of that work.
The problem was everything around them.

With Claude Desktop or Claude Code, every time I switched clients I had to
switch connections: swap keys, change config files, remember which MCP server
pointed where. Leave the last job's connection in place by accident, and an
agent can read the wrong client's data. That's the kind of mistake you only get
to make once.

So I wrote down what I actually wanted:

- Connect each client's tools once, and never think about it again.
- Ask in plain words, and have the work land in the right client's project.
- Make it impossible for one client's work to touch another client's data.
- Let the agent read freely, but stop and ask me before it changes anything live.
- Run it on my own computer, with my own Claude key. No new account, no new cloud.

Ringkeeper is that list, turned into a Windows app.

## How it works

The core idea is small. Every connection (a database, an Airtable base, a GitHub
repo) belongs to one client and is linked to that client's projects. When I ask
for something, a router works out which project I mean and starts a task there.

The task runs Claude Code, but it never gets a key. It gets a short-lived pass
for its own project, and every call to a client's service goes through a gateway
on my computer. The gateway checks that the project may use that connection,
adds the key itself, strips secrets out of the answer, and writes the call to a
log that can't be edited. If the agent asks for another client's database, the
gateway says no, whatever the prompt says.

Changes to production wait for me. I see exactly what will run, the SQL or the
record or the workflow, and approve or deny it, in the app or from Telegram,
Slack or Discord. Anything destructive, like deleting rows, needs me to type the
connection's name. And a change can be undone for seven days:
before it runs, Ringkeeper keeps a copy of only what it's about to change.

## What it's made of

- A Python backend (FastAPI, SQLAlchemy, SQLite on the desktop, Postgres on a
  server) with the rules in one services layer.
- An MCP gateway, one endpoint per kind of service, so Claude Code sees ordinary
  tools.
- A Next.js interface.
- A small Rust shell (Tauri) for the window, the tray, Windows notifications and
  signed updates.

It's open source under MIT, and it runs the same code as a desktop app or as a
Docker stack on a server.

## What I learned

**The rules have to live below the AI.** It's tempting to tell the model "only
use this project's connections". A model mostly follows instructions, and mostly
isn't good enough when the data belongs to someone else. Every guarantee in Ringkeeper is
enforced by code the agent can't talk to: the gateway, the database, the vault.

**Fail closed, and say why.** When Ringkeeper isn't sure an SQL statement only
reads, it treats it as a write. When it can't tell whether a change can be
undone, it says "can't be undone" before you approve it, not after.

**Test against real things.** There are around 900 automated tests, run against
both SQLite and a real Postgres, plus fake Slack, Discord, Telegram, n8n and
Airtable servers. The tests I trust most aren't unit tests of a helper; they're
"two clients, one agent, try to cross over".

**Plain words are a feature.** I went through every message in the app and took
out the words that only made sense to me: instance, gateway, worker, slug. Every
error now says what happened and what to do next. That pass also turned up
errors that were being quietly swallowed.

**AI is a great pair, with a short leash.** I built this with Claude Code doing
much of the typing, and the same discipline applied: small commits, both test
suites green before anything is pushed, and a person clicking through every
screen before a release.

## Where it is now

Ringkeeper is in beta: a Windows installer, free, with in-app updates. It has
agents with their own instructions and connections, routines that run them on a
schedule or when something happens, a demo client to try it on, undo, backups
you can restore on another computer, and a user guide.

If you run work for many clients and this sounds familiar, try it, or tell me
what's missing: [github.com/alexyushkin/ringkeeper](https://github.com/alexyushkin/ringkeeper).
