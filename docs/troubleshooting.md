# Troubleshooting

## A connection says Plan limit

The service answered that your plan with it doesn't include this. The key and the connection are
fine, so another try won't help: Ringkeeper doesn't repeat the call for an hour, and that call
doesn't count against the task. Anything else that fails in the task still does. Upgrade with the
service, then press **Test** on the connection.

If something here doesn't fix it, use **Help › Report a problem** in the app
(or Ctrl+K, "report"). It gathers the version and the end of the logs, removes
keys, tokens, passwords and email addresses, and shows you exactly what's in it
(you can edit it) before you open a GitHub issue or save it as a zip. Nothing
is sent by Ringkeeper itself.

For one connection, open its card's **Details** and use **Copy details for support** or
**Copy tool list** (see [Connections](connections.md#copy-what-a-connection-offers)).

## Windows says "Windows protected your PC"

The beta isn't signed yet. Click **More info › Run anyway**. The installer comes
from the [releases page](https://github.com/alexyushkin/ringkeeper-releases/releases/latest).

## "Can't reach Ringkeeper's backend"

The part of Ringkeeper that does the work stopped. Quit Ringkeeper from the tray
icon and open it again. If it keeps happening, report a problem: the logs say
why it stopped.

## Claude says the key isn't valid

Open **Settings › Claude** and paste the key again. Keys start with
`sk-ant-`. If you're on an Anthropic workspace that needs it, add its ID there
too. Ringkeeper checks the key with Anthropic before saving it, and shows
Anthropic's own message if it's refused.

## Claude is unavailable

When Claude itself can't answer, Ringkeeper says which of these it is, and what it does:

- **Your Claude plan's limit is reached.** Home says when it resets. Nothing new starts
  until then, and tasks and requests wait instead of failing: they go on by themselves after
  the reset. **Try now** next to the notice stops waiting (if Claude still says the limit is
  reached, it waits again), and so does changing the Claude sign-in, key or place in Settings.

Only Claude's own signals count: a task that fails while talking about another service's
"rate limit reached" or "Internal Server Error" is an ordinary failure, not Claude's.
- **Claude is overloaded, limiting how fast requests can go, or can't be reached** (check
  this computer's internet connection). A task is tried again a few times, waiting longer each
  time. A request waits and is tried again after 2, 5 and 15 minutes, then every half hour.
  The conversation says so once, and again when Claude is back. After 12 hours it stops
  trying and offers **Try again**.
- **Claude takes too long to work out where a request goes.** Under a request, the
  conversation says "Finding the right project…", and after 20 seconds "Still working out
  where this goes…". Working out where it goes stops after 90 seconds and starts over at
  once. If that's slow too, the request waits and is tried again like the rest above, and
  the conversation says so once.
- **The sign-in to Claude is missing or expired.** Sign in again in Settings › Claude, then
  send the request again or use **Run again** on the task.

When routing a request fails for any other reason, its reply has a **Try again** button.

## A task stopped when Ringkeeper restarted

A restart or an update stops tasks that were running. They say "Ringkeeper restarted during
this task" and are listed on Home under **Cut short by a restart** for three days. **Resume**
picks a task up where it stopped (one that hadn't got going starts over), and **Resume all**
does them all. Sending a follow-up in the task's conversation works too.

## A connection's test fails

Open the connection and press **Test connection** to see the service's own
message. Usually the key was revoked or expired: paste a new one. A database
must be reachable from this computer (not `localhost` on another machine), and
Ringkeeper never connects to addresses on this computer itself.

## A task went to the wrong project

Ringkeeper asks when it isn't sure, and remembers your answer for next time.
Naming the project in the request ("in globex-portal") settles it. A project's
other names (Clients & projects › the project) help too.

## An approval disappeared

Held actions expire after 24 hours. The task says so, and you can ask it to try
again. Activity shows what happened to each one.

## A Google account stopped working

Its password changed (for mail), it was unused for six months, or its owner
removed Ringkeeper. Open it under Connections and sign in again.

## Where the logs are

`%LOCALAPPDATA%\Ringkeeper\logs`: `backend.log` and `shell.log` (and the
previous run's, `*.previous.log`). They never contain your keys.
