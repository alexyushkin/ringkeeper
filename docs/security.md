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

## The AI never sees a client's key

A task runs Claude Code with only two things: its Claude credential (so it can
think) and a short-lived pass for its own project. Every call to a client's
service
goes through Ringkeeper's gateway on your computer, which checks that the
project may use that connection, adds the key itself, removes any secret from
the answer, and logs the call. One client's project can never reach another
client's connection, whatever the AI asks for.

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

Nothing else: no analytics, no usage statistics, no crash reports. A problem
report only leaves when you send it yourself, after reading it.

## Changes wait for you

Changes to production connections, and anything that reaches real people,
wait for your approval; destructive ones need the connection's name typed. See
[Approvals and safety](approvals.md).

## Reporting a vulnerability

Privately, please: see [SECURITY.md](../SECURITY.md).
