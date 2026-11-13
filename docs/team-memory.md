# Sharing memory with a team

Several people, each with their own Ringkeeper, can keep their agents' notes in one Notion
page, and every Ringkeeper reads what the others saved. There are no team accounts and no shared
keys: each person keeps their own install and their own Notion connection. Only the notes are
shared.

## Set it up

1. **One root page in Notion.** Someone makes a page for everyone's notes, for example
   "Bayside agency memory", in a workspace the whole team can reach.
2. **Each person adds their own Notion connection.** In Ringkeeper, add **Notion memory** as a
   shared tool under Connections, with a token of your own
   ([where to get it](services/notion.md#where-to-get-the-key)). In Notion, open the root page,
   choose **••• > Connections**, and add your connection, so it can read and write there.
3. **Each person clicks Notion for every project and picks the same root.** In **Settings ›
   Memory › Notion for every project**, choose your connection and the root page, and choose
   **Use Notion for every project**.

The first person's Ringkeeper makes a page per client under the root (`Acme › Ringkeeper`) and a
database per project under it (`Acme › Website › Ringkeeper memory`). Everyone after that joins
them: a page or database already there with the title Ringkeeper would give it is used, not made
again. The result says what was joined and what was made.

For this to work, clients and projects need the same names in everyone's Ringkeeper. A project
only one person has gets its own database, made the first time.

**When it's not clear which one,** Ringkeeper asks instead of guessing. That's when two pages or
databases have the title, or one has a title close to it but not the same ("ACME › Ringkeeper",
"Acme › Ringkeeper (old)"). **Notion for every project** then lists them, each with a link, and you
choose one or **Make a new one**. The projects waiting on it are set up when you choose. Until
then, their notes stay in Ringkeeper.

You can also point one project at a database that's already there: in the project's
**Settings › Memory**, choose **Notion**, then **Use a database that's already in Notion**, and
pick it.

## Who saved what

Set **Settings › Memory › Your name** to the name your team knows you by. Every note you or your
tasks save to Notion has it in the **Saved by** column, next to the agent, the date and the task
it came from. Rows people type in Notion themselves are left as they are.

## What's shared, and what isn't

**Shared:** the notes, and only the notes: what agents and people save to each project's memory,
and what's typed in those databases in Notion.

**Not shared:**

- **Connections and keys.** Each Ringkeeper keeps its own, in its own vault. Nobody's database
  password, API key or Notion token goes to anyone else.
- **Approvals.** Each person approves their own tasks' changes.
- **History.** Conversations, tasks, transcripts and the activity log stay in each person's
  Ringkeeper.
- **My memory.** It stays yours. Ringkeeper never offers or joins a My memory database that's
  already in Notion, since it may be someone else's: yours is made new, under a page you choose.

## What stays safe

- **One client's page never holds another client's notes.** That holds for joined pages too: a
  page joined for Acme can't hold Bayside's database, and a database whose title says it's another
  client's can't be picked for a project.
- **A connection that can't write.** If Notion lets your connection read a database but not
  write to it, the project's **Memory** says so. Notes are kept in Ringkeeper meanwhile, and go to
  Notion, oldest first, once it lets your connection write. In Notion, open the page, choose
  **••• > Connections**, and give your connection access.
- **Everything else in [Notion and agent memory](services/notion.md) still applies:** no secrets
  in notes, and notes are read as information, not instructions.
