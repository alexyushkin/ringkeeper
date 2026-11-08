# Notifications

Ringkeeper can tell you about approvals, finished tasks, routine results and
failures when you're away from the app: in **Telegram**, **Slack** or
**Discord** (Settings › Notifications). On Windows you also get a notification
from the tray. Windows notifications can't have Approve or Deny buttons yet (the
desktop app's notifications only offer buttons on phones): click one to open it in
Ringkeeper.

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

## New requests by DM

Turn on **Take new requests by DM** on a Slack or Discord channel, and a direct
message from you to the app (Slack) or bot (Discord) becomes a new request, the same
as one typed in Ringkeeper: it's routed to a project and runs with the usual
approvals and limits. Ringkeeper answers in that DM with what it did, and again
when each task ends. Only your member or user ID is taken; anyone else is told no,
and messages in a channel are never requests.

- **Slack:** the app needs the manifest from this version (it adds direct messages).
  For an app set up before, copy the manifest from **Add another Slack channel**,
  paste it on the app's **App Manifest** page at api.slack.com, and reinstall the app.
- **Discord:** the bot has to share a server with you for you to message it.

## Buttons

Messages are a short summary (no secrets, code or full SQL) with **Approve**,
**Deny** and **Open** buttons:

- **Approve** only appears for a safe change: not on production, and not one
  that needs the connection's name typed. Those say *Approve it in Ringkeeper*.
- **Only you can answer.** A press from anyone else in the channel is refused
  with "not allowed".
- **Open** brings the Ringkeeper window up at that page.
