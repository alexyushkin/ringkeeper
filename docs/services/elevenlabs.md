# ElevenLabs

Voice agents, phone calls and speech, through ElevenLabs' own hosted server.

## Connect it

Under **Connections**, choose **ElevenLabs** and sign in with your ElevenLabs account. You
approve what Ringkeeper may do on ElevenLabs' page, and there's no key to paste: ElevenLabs'
server doesn't accept API keys. Connect one account per client, or your own.

Sign-in works from the desktop app. A server install can't use this connection, because
ElevenLabs only sends a sign-in back to the fixed address on this computer.

Workspaces in ElevenLabs' EU, India and Singapore environments aren't on this tile yet.

## What it can do

The tools are ElevenLabs' own, and **Connections** lists them once you've signed in.

- **Reading runs.** Listing and reading agents, voices, conversations and the like.
- **Anything else waits for your approval, every time:** creating or changing agents, placing
  calls, cloning voices, generating speech. "Trusted for an hour" doesn't apply here, and
  generating speech uses your ElevenLabs credits.
- **Deleting needs the connection's name typed,** and can't be approved from Slack, Telegram or
  a notification.

These rules go by a tool's name. You can make a tool stricter, never looser.

Ringkeeper can't undo anything on ElevenLabs, and the card says so.
