# n8n (automations)

## Where to get the key

In n8n, open **Settings › n8n API** and create an API key
([n8n's guide](https://docs.n8n.io/api/authentication/)). Add your n8n's
address too, like `https://n8n.acme.example`. n8n Cloud's free trial has no API.

## What it can do

- **Read:** list workflows and their runs (executions), get one of each. A list of workflows
  says how many there are in all and how many are on, not only on the page it shows. A list
  of runs says how many there are and how many of each status (succeeded, failed, running...),
  counting the newest 1,000 at most.
- **Change:** turn a workflow on or off, replace a workflow, run a past
  execution again, stop a running one.

## What's held

**Every change waits for your approval**, on any connection. Replacing a
workflow needs the connection's name typed. A replaced workflow stays a draft
unless the agent asks to put it live; approval is refused if someone edited the
workflow after it was held.

## One n8n for several clients

If your n8n holds several clients' workflows, make it your own tool and narrow each client
project's link to a workflow tag or an n8n project. That project's tasks then see and change
only those workflows and their runs. See
[A service you host for clients](../connections.md#a-service-you-host-for-clients).

## Undo

Replacing a workflow, and turning one on or off, can be reverted for 7 days
([Undo](../undo.md)). Re-running or stopping a run can't.

## Automation watch

A routine can start when a workflow run fails, to find out why and propose a fix. See
[Automation watch](../agents-and-routines.md#automation-watch).
