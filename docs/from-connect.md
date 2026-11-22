# Send to Ringkeeper: clients from the small app

Ringkeeper Connect is the small app that only connects your AI apps to your clients' tools.
Ringkeeper is the full app: tasks, approvals before changes, undo, routines and more. Both can
run on the same computer. To bring Connect's clients into Ringkeeper, there's no file to save
and no password to type:

1. Open Ringkeeper once, so it's ready to receive.
2. In Ringkeeper Connect, click **Send to Ringkeeper** at the bottom of the sidebar, then
   **Send to Ringkeeper** again.
3. Back in Ringkeeper, Home shows what came: each client, how many connections, and what can't
   come over. Click **Bring them in**, or **Throw it away**. The first run shows it too.

## What comes over

- **Each client**, with a project for one that has none here yet (named after it). A client
  that's already here keeps its own projects, and its connections are linked to them.
- **Each connection**, as a connection of that client, linked to its projects. Its keys go into
  Ringkeeper's vault. It starts as Production, so its changes wait for your approval, as every
  client connection does.
- **The tools for every client**, as shared tools for all projects.

Connect keeps everything as it was: nothing there changes, and your AI apps keep working with
Connect until you move them to a [pass](passes.md).

## What doesn't

- **Sign-ins made in Connect** (Google, Microsoft and others signed in through a browser) don't
  move: they were made for Connect. Sign in to them again here, from Connectors › Browse
  connectors. The card lists each one.
- **A tool that needs a program this computer doesn't have** (Node.js, say) is listed with what
  it needs.
- **A connection that's switched off in Connect**, and archived clients, stay behind.
- Connect's own per-client rules for what the AI may do don't come over: Ringkeeper's approvals
  and each connection's tool settings apply instead.

## How it's kept safe

Ringkeeper keeps a key pair for this. Its public half is in Ringkeeper's data folder, where
Connect finds it; the private half is in Ringkeeper's vault. Connect locks what it sends with
the public key, so only this Ringkeeper can open it, and the keys are never on disk in the
clear. Once brought in (or thrown away), the file is deleted. Sending again brings in only
what's new.
