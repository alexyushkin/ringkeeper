# Notifications

Ringkeeper can tell you about approvals, finished tasks, routine results and
failures when you're away from the app: in **Telegram**, **Slack** or
**Discord** (Settings › Notifications). On Windows you also get a notification
from the tray.

## Each channel's choices

- **What it sends:** approvals and questions, routine results, failures,
  finished tasks.
- **Quiet hours:** nothing is sent then; what came up is sent when they end (an
  approval only if it's still waiting).
- **Which clients and projects:** all by default.
- **Send test** checks it works, and that your button presses count.

## Telegram

In Telegram, message **@BotFather**, send `/newbot`, and paste the token it
gives you into **Settings › Notifications › Telegram**. Then send your new bot a
message and choose your chat in Settings. Only that chat gets messages, and only
it can answer. You can also turn on requests from Telegram: type or send a voice
note, and you see what Ringkeeper heard before anything is sent.

## Slack and Discord

Both are your own app (Slack, in Socket Mode) or bot (Discord). They connect
out from your computer, so nothing needs a public address. Settings walks you
through creating them step by step: the Slack app manifest to paste, or the
Discord invite link that asks only to see channels and send messages. Then
choose where it posts (a channel or a direct message) and your member or user
ID. Tokens go into the vault.

## Buttons

Messages are a short summary (no secrets, code or full SQL) with **Approve**,
**Deny** and **Open** buttons:

- **Approve** only appears for a safe change: not on production, and not one
  that needs the connection's name typed. Those say *Approve it in Ringkeeper*.
- **Only you can answer.** A press from anyone else in the channel is refused
  with "not allowed".
- **Open** brings the Ringkeeper window up at that page.
