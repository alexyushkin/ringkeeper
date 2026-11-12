# Check my setup

Check my setup sends real requests through your own Ringkeeper, on your own projects and
connections, and reports what happened to each. It uses the real router, real workers and the
real gateway. The [self-test](self-test.md) does the same against made-up clients in a scratch
folder; this checks what you've actually set up.

**It only reads.** Nothing it starts can change anything:

- Its tasks can't edit files or run commands (no Edit, Write or Bash), and make no branch in a
  project's folder.
- The gateway holds every write from them for your approval, even one that would run without
  asking anywhere else. Nothing held is approved by the check: it's left for you in
  **Approvals**, and the report says so.
- A request to go ahead on a production connection, or to let a project use the web, is left for
  you the same way.
- They keep no memory notes, and answering "which project?" doesn't teach the router anything.

## What it sends

- One read-only request per connection, like the ones Home suggests: "List the tables in Acme
  web's database", "List the open pull requests in Acme web's GitHub repository". It goes in the
  first project the connection is linked to: a connection linked to several projects, or to all
  of them, is checked once. When a project has two connections of a kind (two databases, say),
  each request names its connection. A request names a connection by its unique name
  (`acme-google`), not the name the app shows, which two can share. A Google account's request
  follows what it was given: its
  calendar if it has Calendar, its unread mail if it only has mail, and so on. One given nothing
  it can read (only sending mail, say) is tested like an MCP server, below.
- An MCP server, an add-on tool's API or an HTTP API isn't sent a request: Ringkeeper connects
  to it itself, as its **Test** button does, and counts the tools it offers (an HTTP API's
  endpoints, if it has an OpenAPI address). No model is asked, so it costs nothing, and the
  connection's card shows the result.
- For a project none of those went through the router for (its connections were checked in
  another project, or tested directly), one request of its own, "Explain what's in Acme shop's
  folder, in a few lines", to check that its requests reach it.
- A question about your setup: "Which projects do I have, and which connections does each one
  use?"
- A web lookup.
- A follow-up in the first conversation, to check that it carries on there.

A connection linked to no project isn't sent anything: no task could use it. The report lists it
under **Gaps**, and its card says **Not linked to any project**.

If it's asked which project a request is for, it answers with the project the request was
written for. If a task asks a question, it answers "only read". Both are in the report.

## Run it

In the app: **Settings > Advanced > Check my setup**, then **Check my setup**. It sends three
requests at a time, and each one's row shows as soon as it's back. You can keep using Ringkeeper
while it runs, and **Stop after these** sends no more.

In a terminal, with Ringkeeper running (the requests go to it):

```
ringkeeper scenarios run --mine
```

It stops sending new requests at an estimated $5 (on an API key), or whatever you give
`--ceiling`. On a subscription nothing is billed per request, but it counts toward your usage.

## The report

One row per request: where it was routed, whether it asked anything, which tools ran, what came
back or the error, and what it cost. Then the gaps. The report is kept in the `setup-check` folder
in your Ringkeeper data folder, by day, and shown in the same section of Settings.

## Its conversations

Its conversations are titled "Check my setup: ..." and tagged. Archive them all, with their
tasks, in one click: **Archive them** in the same section, or

```
ringkeeper scenarios archive --mine
```

Archived conversations stay searchable, and can be brought back.
