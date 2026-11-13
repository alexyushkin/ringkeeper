# Concepts

You ask, and Ringkeeper does the work for the right client. That's the whole app:
agents and routines are extras, for work that should start without you. Once these
eight ideas click, the rest follows.

![How a request flows: you ask, it finds the project, Claude does the work, live changes wait for your OK, and it reports back](images/how-it-works.svg)

**Request.** What you ask, in your own words: typed or spoken, in the app or on
Telegram. It starts a conversation, and this is how nearly everything begins.

**Client.** Someone you do work for, like General Informatics LLC. Each client's projects and
connections are kept apart from every other client's: a task for one client can
never reach another client's data.

**Project.** One piece of a client's work: a website, an app, a set of
automations. Every request goes to one project, and its task works in that
project's folder. A project has other names people use ("the geninfos site"), so
Ringkeeper can tell which one you mean.

**Connection.** A client's service that their projects can use: a database,
Airtable, n8n, Vercel, GitHub, a Google account, Twilio, an MCP server.
Ringkeeper keeps the key in its vault and makes each call itself; the AI never
sees a key. A connection is marked production, staging or development.

**Shared tool.** A tool of yours that holds no client's data, like a web
browser or files on this computer. You can give it to any client's projects, or
to all of them.

**Agent.** Optional. A named helper inside one project, with instructions it always
follows and only the connections you give it. *An agent is who does the work.*

**Routine.** Optional. When an agent runs on its own: on a schedule, when something
happens (an email, a file, a webhook), or when you press Run. *A routine is when
it runs.*

**Hold.** A change to live client data (a production connection), or anything
that reaches real people (an email, a text), waits for your OK in Approvals.
Nothing happens until you approve it, and everything is logged.

## A request, start to finish

1. You ask, in plain words: "Add last week's leads to General Informatics' Airtable."
2. Ringkeeper picks the project (it asks if it isn't sure) and starts a task.
3. The task reads what it needs through the project's connections.
4. A change to production is held. You see exactly what would run, and approve
   or deny it.
5. The task goes on with the result and reports back in the conversation. Most
   data changes can be undone for 7 days ([what can and can't](undo.md)).

A task that couldn't do what you asked ends as **Not done**, never Done, and says why: on its
card, on its page, and in the chat you asked from. When what it lacked is a connection the
client has but the project isn't linked to, it offers one click, like "Link General
Informatics' Google to Website and retry", in the app and as a button in the chat.

When it did part of it, its card and page say **Partly done**, with what was done and what
wasn't. It still counts as not done.

A task's status says what happened, from what the gateway recorded, never from what was asked:

- **Done:** a change it made through a connection ran (the activity log shows it), or it
  changed files.
- **Answered:** it read and reported, and changed nothing, as asked.
- **Nothing changed:** a change was asked for or tried, and none ran (it was denied, or the task
  left things as they were). A request that says to do something ("handle it", "mark",
  "update") asked for a change, even if the task only read. A task that couldn't do the change
  and changed nothing says this too, with why under it.
- **Refused: other client's data:** it reached for another client's connection and the gateway
  refused.

When work starts, the conversation says only where it went: "Working on it in Website." What the
task will read or change is for the task to report, since it may decline. A request that names
another client's connection, one of its projects, or the client itself also gets, up front,
"This went to Bayside's project. Tasks there can't reach Acme's connections." Split between two
clients' projects, it says that for each.

A connection is reached only through its own tools. When they can't do something, the task
stops there and says so, instead of trying the same connection another way.

A task does all its work before it answers: nothing of it runs in the background or after it
ends, so an answer never says the rest is still being fetched.

### A task's changes to files

A task that works on a project's code (a feature, a fix, content) works on a branch of its
own, and leaves the folder checked out as it found it. To use the changes, open the task's
**Changes** tab: **Keep these changes** merges them into the project's main branch, and
**Discard** throws them away. A follow-up in the same conversation keeps working on the
same branch, so it sees the earlier work. A task that only read changes nothing and leaves
no branch.

One code task works in a project's folder at a time; another waits until it's done. If the
folder has edits you haven't committed, a code task doesn't start and says so: commit or
stash them first. Only the files the task itself edited go on its branch. A file someone
else edited during the run stays out, and the task names it. After a command the task ran,
it can't tell who edited what, so every edited file goes on the branch and the task names
the ones it didn't edit itself.

Work with data or connections, and general work like a web lookup, doesn't use git at all:
no branch, no waiting for the folder, and uncommitted edits don't stop it. If such a task
does write files in the folder, its page says so ("Changed 2 files in the folder, not on a
branch").

### Files a task makes

The files a task made or changed are listed on its page (the **Changes** tab) and under its
result in the conversation. In the desktop app each has **Open** and **Show in folder**. A
code task's files are on its branch until you keep them: they open from a copy, and your
folder isn't touched. Only documents open: PDFs, Office files, text, Markdown, CSV, JSON
and pictures. Anything else (a program, a script, a web page) is only shown in its folder,
so nothing a task wrote runs by a click. A table in a result has **Copy** (to paste into a
spreadsheet) and **Download CSV**. In the desktop app the CSV is saved in your Downloads
folder (as `table.csv`, or `table (2).csv` when that's taken; nothing is overwritten), and
**Show in folder** shows it there.

### Where a request goes

The client and project chosen at the top of the app are a firm choice for new requests,
like an @-mention: the box says "Sending to Alexey › My Project", and a request sent then
goes to that project without asking. Two exceptions: an @-mention of another project or
client in the request wins, and when the request is clearly for another client's project,
it asks once ("This looks like it's for Acme, but My Project is selected"), with both as
buttons.

Work that needs no client's connection, like a web lookup, writing, or summarizing text
you pasted, runs in the chosen project. With nothing chosen, it runs in your own client's
default project: on that client's page, open **Settings › Your own work** and turn on
"... is me".

A follow-up stays where its conversation's work is. Once a conversation has a task in a
project, a later message there that names no other project, client or connection runs in
the same project, even if it needs no connection ("Thanks. Now say that again in two
sentences").

Whether a project's tasks can use the web is its own setting (the project's Settings tab,
**Web access**), apart from how independently it works. Left at the default, it's on for
your own client's projects and off for a client's, so their data doesn't leave unless you
allow it. A request that needs the web in a project where it's off says so and asks once,
with **Allow web for this project and retry** (in the app and in your chats).

A question about your setup ("how many connections do we have?") is answered from
Ringkeeper's own records, without a project. A request that names a connection and asks to
use or read it ("Use Demo CRM to list the tools it offers") is always a task in a project
linked to that connection, even when it asks what the connection can do. Asking where a
connection is linked, or whether its token is about to expire, is still a question about
your setup.

### When it asks which project

Ringkeeper asks only when the work clearly belongs to one project and more than one fits,
and says why in one line, like "Both Website and Blog have a contact form." A request that
names a client but none of its projects, for work on one project's own things ("add a file
to Acme's project folder"), is always asked about when the client has more than one. It
doesn't ask:

- **With one option.** If only one project could be meant, the work goes there. That
  includes work that needs a connection only one of the client's projects has, like
  their mailbox linked to just one of them.
- **Again in the same conversation.** Once you've picked, later requests there go to the
  same project unless they name another.
- **For work any of a client's projects would do**, like reading their email or Google.
  That goes to the client's **default project**: their only project, or the one you choose
  on the client's Overview tab. The first time it would have asked, the question offers
  to make your pick the default.

You can answer by replying, too, in the app or a chat: send just the project's name ("Blog",
"the Acme one", "Alexey, My Project") right after the question, and the request you sent
runs there, with no second question. A project's own name wins over another project's
nickname that's part of it: "Demo site" means Demo site, even if Demo shop is also called
"demo". The same request sent again while it's still running doesn't start it twice: the
reply says it's already working on that, with a link. "None" or "neither" starts nothing. When it asks to go
ahead on production, "yes" or "do it" is the go-ahead. If it said it couldn't tell where a
request goes, a reply that's just the project's name sends that request there.

Only a reply that's just a name, sent right after, counts as the answer. A message that asks
for something ("List Acme's active workflows") is a new request even if it names a project,
and the question stays open. Other short follow-ups, like "use my own Google instead",
continue the open request.

See also: [Connections](connections.md), [Agents and routines](agents-and-routines.md),
[Approvals and safety](approvals.md).
