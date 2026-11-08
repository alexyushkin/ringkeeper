# Agents and routines

*An agent is who does the work; a routine is when it runs.*

## Agents

An agent is a named helper in one project (**Agents › New agent**, or a
project's Agents tab):

- **Instructions** it follows every time: tone, what to check, what never to
  touch.
- **Connections** it may use: some of the project's. A shared tool only when
  you tick it. Ringkeeper refuses its calls to anything else.
- Its own **model**, **effort** and **how independently it works**, or the
  project's.

In a request, ask it by name: "ask the follow-up agent to draft replies to
today's leads."

## Routines

A routine runs one agent with a prompt (**Routines › New routine**: the
project, then its agent; you can create the agent right there):

- **On a schedule:** every day, weekdays, chosen days, monthly or every few
  hours, at a time in your time zone. After the computer slept, one run makes
  up for the missed ones.
- **When something happens:** a new email matching a search, a new Airtable
  record, a file added to the project's folder, or its webhook being called (on
  this computer only). Or something going wrong: see Automation watch below.
- **By hand:** Run now.

New routines start paused, with **Test run**. Each run is an ordinary task:
reads run, changes wait for your approval like any other. Limits per run (steps,
minutes, a dollar cap with an API key) and runs per day keep it in check. Pause
one routine, a project's, or all of them (also from the tray).

Templates to start from: fix a failed automation, daily digest, new-lead follow-up
draft, weekly report, data sync check. Below them are [recipes](recipes.md): an agent with its
routines, from the starters, a file or a link.

## Automation watch

A routine can watch a client's automations and start when one goes wrong:

- **An n8n workflow run fails** (all of a connection's workflows, or one).
- **A Vercel deploy fails** (one Vercel project).
- **A GitHub check fails** on a branch's latest commit, or **an issue is opened**.
- **A website stops answering**: an error, or no answer in 15 seconds.

Each is checked every 5 minutes, reading only. What's already there when you turn
the routine on doesn't count; each new failure starts one run. A website being down
is one event until it answers again.

Ringkeeper reads n8n, Vercel and GitHub through the project's own connection, the same
way a task does: the project's connections only, never another client's, with keys
and secrets kept out of what the routine sees. A website is checked from this
computer with no key; only whether it answered is kept.

The run gets `{{event.what}}` (what went wrong, in a sentence), `{{event.link}}`
where there is one, and the details, like `{{event.execution_id}}` for n8n. The
**Fix a failed automation** template starts from these: the agent finds out why,
says it in plain words, and proposes a fix, which waits for your approval like any
change to production.

Home shows a line for it: how many routines are watching, the last problem they
found, and any watch that can't look (a connection that stopped working, say).
