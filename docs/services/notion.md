# Notion and agent memory

Agents can keep notes between runs: decisions, preferences, how a client's things are set up.
Each project has its own notes. Choose where they live for every project at once, in
**Settings › Memory › Notion for every project**, or for one project in its **Settings**, under
**Memory**:

- **Ringkeeper (local)** is the default. Notes stay on this computer.
- **Notion** keeps them in a Notion database you can read and edit yourself.

Each project's **About** says where its notes go, with a link to its Notion database.

An agent can also have its own choice, in its settings. It can keep notes locally while the
project uses Notion, or the other way round.

## Where to get the key

Notion memory uses its own connection, **Notion memory**, not the Notion connection in the
catalog. That one reaches whatever your account can see. This one reaches only the pages you
share with it, so Notion itself keeps agents' notes where you put them.

1. In Notion, open [your connections](https://www.notion.so/profile/integrations) and create an
   internal connection. A workspace owner has to do this. Copy its token.
2. Add **Notion memory** under Connections and paste the token. Add it as a shared tool to use
   it for every project, or for one client.
3. In Notion, open the page the notes go under, choose **••• > Connections**, and add it.
4. Choose where notes go:
   - **For every project:** in **Settings › Memory › Notion for every project** (or on the
     connection's card), pick the connection and one root page, and choose **Use Notion for
     every project**. It needs a shared connection.
   - **For one project:** in the project's **Settings**, open **Memory**, choose **Notion**, pick
     the connection and the page, and save.

For one project, choose **Create a new one** and a page: Ringkeeper makes a database under it
called `<Client> › <Project> › Ringkeeper memory`, with one row per note: the note, the agent, the
date, the task it came from and who saved it. Or choose **Use a database that's already in
Notion** and pick it. A database whose title says it's another client's, or anyone's My memory,
can't be used.

For every project, it makes a page per client under the root page (`<Client> › Ringkeeper`) and
each project's database under its client's page, so one client's page never holds another
client's notes. A page or database already there with that title, made by someone else's
Ringkeeper, is joined instead of made again; when two could be the one, or a title is close but
not the same, it asks which. See [Sharing memory with a team](../team-memory.md). A project already in Notion keeps its place. A new project gets its database when
its first task starts; if Notion doesn't answer then, its notes stay here that time, and it's
tried again on the next task. **Stop for new projects** keeps every project where it is now.

A Notion memory connection only holds notes: it isn't linked to projects, and no task calls it as
a tool. Its **Test** button still checks the token. Links made before 0.1.0-beta.38 were removed.

## What agents keep

Every task starts with its project's latest notes (and Shared notes and My memory, if they're on),
so it doesn't have to search for what an earlier task found. Before it finishes, it saves what
identifies this client's things: names and naming patterns, IDs, the filters that find them, and
which base, table, workflow or page does what; decisions and corrections you gave; how this client
wants things done; and a limit of a connection when it blocks a kind of request. One fact per note,
each understandable on its own. Never counts, dates, statuses or anything that changes, never how a
tool works in general, and never the task's result or a secret: one to three short notes, or none.

A good note: "Bayside's workflows in agency-n8n are named 'Bayside #N' and 'Mr. Gutter #N'. Filter
by name." A bad one: "n8n has 184 workflows; list_workflows returns 50 per page."

A note that says what one already says, give or take a few words, is refused as "already noted".

A task's answer mentions memory only when it saved something, and says exactly where:
"this project's notes (Bayside, in Notion)", "My memory" or "shared notes". Its page and the
conversation show **Saved to memory** under the result, with each note and the same words. Check my
setup only reads, so it keeps nothing.

### A note in Notion

Each note is a row in the project's database:

- **Note**: a short title, up to about 80 characters. The whole note is in the row's page.
- **Kind**: Where things live, How we do it, Gotcha or Decision.
- **Agent**: the agent that saved it, or Ringkeeper when no named agent ran. Empty for a note you
  added yourself.
- **Source task**: the first words of the request, then the task's ID.
- **Saved by**: your name from **Settings › Memory › Your name**, on notes you and your tasks save.
  Empty when it isn't set, and on rows people type in Notion.

A database made before the Kind or Saved by column existed gets it added the next time a task uses
it, when Notion allows; its rows aren't changed. A row you type with only a title still counts as a note.

You can add notes yourself, with the same rules and in the same place:

- **Add a note** on the project's **Memory** (in its Settings).
- **Remember this** under a result or a reply: edit the text, and save it.
- Say **"remember that …"** in a conversation. When it's clear which project (the one chosen at the
  top, the one the message names, or the one the conversation is about), it's saved straight
  away, with no task, and the reply says what was saved and where. Otherwise the request is
  routed as usual.

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

If Notion answers but won't let the connection write to the database (it was shared with it
read-only, or not at all), the project's **Memory** says so. Notes are kept in Ringkeeper
meanwhile, agents still read them, and they go to Notion, oldest first, once a write goes
through.

## What's held

- **Reading and adding notes run by themselves,** and each one is in Activity.
- **No secrets.** A note with a token, a password, or a card or bank number is refused, and the same
  things are hidden from agents if someone types them in Notion. A project's notes may keep a
  business contact's email address.
- **Notes are treated as information, not instructions.** Agents are told so, and a note can't
  change what needs your approval.

## Good to know

- A client's memory page can't be attached to another client's project, and the page above it
  can't be shared between clients either.
- A client's Notion connection only works for that client's projects.
- A note is at most 2,000 characters.

## Shared notes

**Settings > Memory > Shared notes** is one Notion page of your own knowledge across clients. It needs a
shared connection. Every project can read it, unless you turn it off in that project's Memory, and
agents can't write there unless you allow it. Keep client data out of it.

## My memory

**My memory** is for what's yours, across every client: your preferences, your style, how you work.
It's a Notion database of your own, so it works on any Notion plan.

1. Add **Notion memory** as a shared tool, and share a page with it in Notion.
2. Open **Settings > Memory > My memory**, pick the connection and the page, and save.

Ringkeeper makes a database called `My memory` under that page. Agents search it when they start,
and add lasting preferences when they finish. You can add and edit rows there too. A project can
leave it out, in its own **Memory** settings.

## What your own memory can't hold

This covers My memory, Shared notes and Notion AI memory:

- **Nothing about a client.** A save naming a client, a project's full display name, or the address
  of a client's connection is refused. Names match as whole phrases, and one common word on its
  own, like website, app, crm or portal, never counts.
- **Nobody's details.** Email addresses, phone numbers and street addresses are taken out, and the
  rest is kept.
- **No secrets.** A token, a password, or a card or bank number is refused.

The check goes by names, so a client's fact that doesn't name them can't be caught. Agents are told
to keep those in the project's own memory.

## Notion AI memory

**Needs Notion Business plan or higher,** with Notion AI. It's off until you turn it on. It uses
Notion's own personal memory instead of a database of yours. On other plans, use My memory.

1. Add **Notion** from the catalog as a shared tool, and sign in with your own account. A token
   won't do: memory needs a signed-in person.
2. Open **Settings > Memory > Notion AI memory** and turn it on.

Memories must be on for you and your workspace: in Notion, **Settings > Notion AI > Memory > Use
memories**. If Notion doesn't offer the tools, Ringkeeper says so. Press **Refresh its tools** after
changing that.

- **Searching, reading and saving run by themselves.** Saves and refusals are in Activity.
- **Deleting a memory needs the connection's name typed.** Editing one waits for your approval.
- **A save about someone else is refused,** and so is any memory call on a client's connection.

## Undo

Rows deleted from Ringkeeper can be restored from Notion's trash. Local notes can't be undone.
