# Notion and agent memory

Agents can keep notes between runs: decisions, preferences, how a client's things are set up.
Each project has its own notes. Choose where they live in the project's **Settings**, under
**Memory**:

- **Ringkeeper (local)** is the default. Notes stay on this computer.
- **Notion** keeps them in a Notion database you can read and edit yourself.

An agent can also have its own choice, in its settings. It can keep notes locally while the
project uses Notion, or the other way round.

## Where to get the key

Notion memory uses its own connection, **Notion memory**, not the Notion connection in the
catalog. That one reaches whatever your account can see. This one reaches only the pages you
share with it, so Notion itself keeps agents' notes where you put them.

1. In Notion, open [your connections](https://www.notion.so/profile/integrations) and create an
   internal connection. A workspace owner has to do this. Copy its token.
2. Add **Notion memory** under Connections and paste the token. Add it for a client, or as a
   shared tool.
3. In Notion, open the page the notes go under, choose **••• > Connections**, and add it.
4. In the project's **Settings**, open **Memory**, choose **Notion**, pick the connection and the
   page, and save.

Ringkeeper makes a database under that page called `<Client> › <Project> › Ringkeeper memory`,
with one row per note: the note, the agent, the date and the task it came from. You can pick a
database that's already there instead.

## What it can do

- **Agents** have two tools, `recall` and `remember`. Neither takes a place: a run reaches its own
  project's notes and nothing else, and it can't delete a note. Your other Notion connection keeps
  its own holds.
- **You can add a row** in Notion, or have Claude or Claude Code add one. It's picked up on the next
  run. Text written inside a row's page counts too.
- **Edits in Notion win.** Change a note there, and that's what agents read.
- **Delete a row and it's forgotten.** Deleting a note in Ringkeeper moves it to Notion's trash.

If Notion doesn't answer, nothing is forgotten. Agents read the last copy and are told it may be
out of date.

## What's held

- **Reading and adding notes run by themselves,** and each one is in Activity.
- **No secrets.** A note with a token, password, card or bank number, or an email address is
  refused. The same things are hidden from agents if someone types them in Notion.
- **Notes are treated as information, not instructions.** Agents are told so, and a note can't
  change what needs your approval.

## Good to know

- A client's memory page can't be attached to another client's project, and the page above it
  can't be shared between clients either.
- A client's Notion connection only works for that client's projects.
- A note is at most 2,000 characters.
- Phone numbers and street addresses aren't caught by the filter, so keep agents' instructions
  clear about them.

## Shared notes

**Settings > Memory > Shared notes** is one Notion page of your own knowledge across clients. It needs a
shared connection. Every project can read it, unless you turn it off in that project's Memory, and
agents can't write there unless you allow it. Keep client data out of it.

## Your Notion memory

Notion can keep personal memories for you, and your agents can use them for what's yours, across
every client: your preferences, your style, how you work.

1. Add **Notion** from the catalog as a shared tool, and sign in with your own account. A token
   won't do: memory needs a signed-in person.
2. Open **Settings > Memory > Notion memory** and turn on **Use my Notion memory**.

It needs Notion AI, which is part of Notion's Business and Enterprise plans, and memories on for
you and your workspace: in Notion, **Settings > Notion AI > Memory > Use memories**. If Notion
doesn't offer the tools, Ringkeeper says so. Press **Refresh its tools** after changing that.

Agents search it when they start, and save lasting preferences when they finish.

- **Searching, reading and saving run by themselves.** Saves and refusals are in Activity.
- **Deleting a memory needs the connection's name typed.** Editing one waits for your approval.
- **Client knowledge never goes there.** A save that names a client, a project or one of their
  addresses is refused, and so is one holding a secret, an email address, or anything about other
  people. That knowledge stays in the project's own memory. The check goes by names: a client's
  fact that doesn't name them can't be caught, so agents are told to keep them out too.
- A client's Notion connection can't use this, and memory calls on one are refused.

## Undo

Rows deleted from Ringkeeper can be restored from Notion's trash. Local notes can't be undone.
