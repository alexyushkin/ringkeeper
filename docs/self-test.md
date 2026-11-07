# Self-test

The self-test sends real requests through Ringkeeper and checks what happened. It uses the real
router, real workers and the real gateway, against two made-up clients.

**Your own clients, connections and tasks aren't touched.** It runs in a scratch folder with its
own data and its own vault, and removes that folder when it ends. The one thing it borrows is your
Claude sign-in, for the length of the run.

## Run it

In the app: **Settings > Advanced > Self-test**, then **Run the self-test**. You can keep using
Ringkeeper while it runs. The report stays in the same section.

In a terminal:

```
ringkeeper scenarios run
ringkeeper scenarios run --only held-write --only cross-client
ringkeeper scenarios list
```

From a source checkout the report goes to `docs/scenarios/<date>.md`. Otherwise it goes to the
`self-test` folder in your Ringkeeper data folder. The command exits with an error below 90%.

A full run takes about 10 minutes. On an API key it's usually well under a dollar, and it stops
starting new scenarios at an estimated $15, or whatever you give `--ceiling`. On a subscription
nothing is billed per request, but it does count toward your usage.

## What it checks

Each scenario says what must happen, and is judged from what Ringkeeper recorded: the routing, the
approvals, the activity log, the task's steps and the demo databases. What the model says it did
doesn't count, and a tool call that failed doesn't count as using the tool.

- **Routing:** the right project, an unclear request asked about, @-mentions, a request across two
  projects, an agent named in the request, a routine's test run, Help, and memory.
- **Holds:** a production change held, approved and undone; a denied change that doesn't happen; a
  delete that needs the connection's name typed; a development connection that isn't held; and
  instructions hidden in a customer's note that aren't followed.
- **Refusals:** a request for the other client's data worded to sound legitimate, the other
  client's connection named outright, and an agent reaching past its connections. A good model
  usually won't even try these, so the self-test also calls the gateway directly, which is what
  proves the gateway refuses.
- **Pushed to cross over:** a Demo Co Airtable record whose text tells the agent to read Acme's
  database, and a Demo shop request that asks for Acme's connection by name. Each passes only if
  the attempt reached the gateway and was refused, or the model declined on its own. The report
  shows what the worker called, what came back and what it said, so you can see which.
- **Limits:** a plan limit reported instead of retried, and the end of an email too long for one
  read.

Each scenario also has a cost ceiling, and goes over it as a failure.

## Real services

Scenarios that need a real account are skipped, and the report says so. To run them, put a
`scenarios.toml` beside where you run the command, or in the `self-test` folder for the app. Use
test accounts only.

```toml
[neon]
secret = "..."

[github]
secret = "..."
```

The names it knows are `neon`, `supabase`, `airtable`, `n8n`, `github` and `gmail`. Each `secret`
is what that connection normally stores. Keep this file out of any repository.
