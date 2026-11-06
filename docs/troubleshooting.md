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
