# Security: what's stored where, and what leaves your computer

Ringkeeper runs on your computer. There's no Ringkeeper account and no
Ringkeeper server: nothing about your clients goes to the people who make it.

## What's stored where (desktop app)

| What | Where | How it's protected |
| --- | --- | --- |
| Clients, projects, connections (names, settings), agents, routines, tasks, conversations, approvals, the activity log | `%LOCALAPPDATA%\Ringkeeper\ringkeeper.db` | Your Windows account. The activity log can't be edited or deleted, by you or by Ringkeeper. |
| Keys and tokens (Claude, every connection, Telegram/Slack/Discord, your Google app) | `%LOCALAPPDATA%\Ringkeeper\vault.json` | Each one encrypted (AES-256-GCM) with a key derived from the master key. |
| The master key | Windows Credential Manager (`Ringkeeper/master-key`) | Your Windows sign-in. Without it the vault can't be read. |
| Kept changes, for [Undo](undo.md) | `%LOCALAPPDATA%\Ringkeeper\undo` | Your Windows account. Deleted after 7 days. |
| Logs | `%LOCALAPPDATA%\Ringkeeper\logs` | Secrets are removed before anything is written. |
| Project folders | Wherever you chose (new ones in `Documents\Ringkeeper`) | Your Windows account. |
| Backups you make | Where you save them | Encrypted with the password you chose ([Backup](backup.md)). |

## Signed links are taken out

Some web addresses work for whoever holds them, like a Lemon Squeezy customer portal link or
an S3 or Azure download link. Ringkeeper keeps where such a link points and removes the rest
from logs, the audit log, tool results and conversations. A task can't pass one on: a tool call
that carries one is refused. To send someone a link like that, use the service itself.

## A task knows when it has only part of something

A long answer never just stops. Every tool that returns part of something says how much there
is and where the next part starts, and a task is told to read the rest before it answers, or
to say its answer is based on part of it. A long result that can't simply be asked for again,
such as the answer to an API call, is kept in memory for about half an hour, for that task
only, after secrets are removed from it.

## Keys stay out of a task

A task runs Claude Code with two short-lived passes for its own project, and no
keys. One reaches its connections, through Ringkeeper's gateway, which checks
that the project may use that connection, adds the key itself, removes any
secret from the answer, and logs the call. The other reaches only the model,
through Ringkeeper's model proxy, which adds your API key, subscription or cloud
sign-in on the way out and counts the tokens. Both end with the session. One
client's project can't reach another client's connection through these.

**The desktop app isn't a sandbox.** On the desktop, the AI runs as your Windows or
Mac user. Ringkeeper's holds and gateway control what it does through connections,
but they don't wall it off from your computer. No key is in its environment or its
own files, yet a script written to do it could still reach anything your user can:
your files, the system keychain and the master key in it, and Ringkeeper's own
files. A task told to do that by something it read is the risk to think about.

For strong isolation, turn on **Settings > Security > Run tasks in Docker**, or use
server mode. A task in Docker sees only its project's folder, so it has no access to
the vault, the keychain or the rest of the computer's files. We recommend it for client
work. It's off by default and needs Docker Desktop, plus a one-time image download.
The image doesn't include Claude Code: the first task in Docker downloads the version
this release pins from npm, checks it against the checksum the release recorded, and
keeps it for the next tasks, where a task can't change it.

Three things to know about it on the desktop:

- **If Docker isn't ready when a task starts, the task still runs,** on this computer
  and without that isolation, and says so at the top of the task.
- **A task can still change its project's folder,** as it's meant to.
- **A container can reach this computer's network,** which it needs for the gateway. A
  service on your computer that trusts anything local isn't protected from it.

## What leaves your computer

- **To Anthropic:** your requests, and what tasks read and do, so Claude can
  work on them (with your API key or subscription, under Anthropic's terms).
- **To the services you connect:** the calls tasks make (Airtable, your
  database, GitHub, Google, and so on), with their keys.
- **To Telegram, Slack or Discord, if you set them up:** short summaries of
  approvals and finished tasks (never keys, code or full SQL).
- **To GitHub:** the update check (`latest.json` from the releases page) and
  the update download.
- **To the MCP registry and Hugging Face, only when you use them:** catalog
  searches, and the download of the on-device voice model.
- **To PyPI and GitHub, only if you download the scan reader:** the request for its
  files. Scans themselves are read on this computer and never sent.

Nothing else: no analytics, no usage statistics, no crash reports. A problem
report only leaves when you send it yourself, after reading it.

## Changes wait for you

Changes to production connections, and anything that reaches real people,
wait for your approval; destructive ones need the connection's name typed. See
[Approvals and safety](approvals.md).

## Reporting a vulnerability

Privately, please: see [SECURITY.md](../SECURITY.md).
