# Changelog

Each release of the desktop app has an entry here; the release workflow
publishes it as the release notes and the app shows it with the update.

## 0.1.0-beta.1 (2026-10-26)

The first beta of the desktop app for Windows.

- **One installer, nothing else to install.** Ringkeeper installs for your
  user only (no administrator rights) and brings what it needs with it:
  Python, git for its workers, and the Claude Code engine its agents run on.
  Your data stays in `%LOCALAPPDATA%\Ringkeeper`.
- **Projects bound to their own services.** Each client's projects reach only
  that client's databases, Airtable bases, Vercel projects, n8n and other
  connections, connected in one click where the service allows it.
- **Approvals.** Writes to production, messages and other risky steps wait for
  you, in the app or in Telegram.
- **Plans first for features**, models and effort per role, and a spending cap
  per task that asks before going further.
- **Voice requests**, transcribed on your computer. The speech engine and model
  download the first time you use voice (the size is shown first), so the
  installer stays small for everyone who doesn't.
- **Updates** are downloaded in the background and offered with "Update ready,
  restart when you're done". Nothing restarts on its own, and updates never
  touch your data.
