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
  this computer only).
- **By hand:** Run now.

New routines start paused, with **Test run**. Each run is an ordinary task:
reads run, changes wait for your approval like any other. Limits per run (steps,
minutes, a dollar cap with an API key) and runs per day keep it in check. Pause
one routine, a project's, or all of them (also from the tray).

Templates to start from: daily digest, new-lead follow-up draft, weekly report,
data sync check.
