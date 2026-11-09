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

### Where a request goes

The client and project chosen at the top of the app are a firm choice for new requests,
like an @-mention: the box says "Sending to Alexey › My Project", and a request sent then
goes to that project without asking.

Work that needs no client's connection, like a web lookup, writing, or summarizing text
you pasted, runs in the chosen project. With nothing chosen, it runs in your own client's
default project: on that client's page, open **Settings › Your own work** and turn on
"... is me". If the project's tasks can't use the web (its autonomy is strict or
standard), Ringkeeper says so plainly instead of starting.

### When it asks which project

Ringkeeper asks only when the work clearly belongs to one project and more than one fits,
and says why in one line, like "Both Website and Blog have a contact form." It doesn't ask:

- **With one option.** If only one project could be meant, the work goes there.
- **Again in the same conversation.** Once you've picked, later requests there go to the
  same project unless they name another.
- **For work any of a client's projects would do**, like reading their email or Google.
  That goes to the client's **default project**: their only project, or the one you choose
  on the client's Overview tab. The first time it would have asked, the question offers
  to make your pick the default.

See also: [Connections](connections.md), [Agents and routines](agents-and-routines.md),
[Approvals and safety](approvals.md).
