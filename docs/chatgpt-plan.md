# Your ChatGPT plan

In the desktop app, Ringkeeper can use your ChatGPT plan for **sorting requests** and for
**the Help assistant**, instead of Claude. It signs in with OpenAI's **Sign in with ChatGPT**:
no API key, and nothing to pay on top of your plan. Tasks still run on Claude for now.

## Turning it on

1. Open **Settings › Models** and click **Continue with ChatGPT**.
2. Your browser opens OpenAI's page. Sign in and allow Ringkeeper to use your plan.
3. Back in Settings, choose a ChatGPT model for **Sorting requests**. The list shows the
   models your plan offers, marked **ChatGPT plan**.

Ringkeeper never sees your ChatGPT chats. Its sign-in is kept in Ringkeeper's vault, like a
key, and is refreshed every hour.

## Usage and limits

What Ringkeeper uses comes out of your plan's own limits, the same ones ChatGPT and Codex
use. In ChatGPT's settings you can also set a limit for Ringkeeper alone: click **Manage
usage** next to the model, or open chatgpt.com/settings/usage.

When the limit is reached, nothing moves to other billing. Requests wait and Ringkeeper sorts
them again later, and the conversation says why. Claude's work isn't paused by it.

**Is it for any plan?** OpenAI offers it to eligible ChatGPT plans. If your account or
workspace can't use its plan in other apps, Ringkeeper says so: choose a Claude model for
Sorting requests instead.

**Why only in the desktop app?** OpenAI offers plan usage to open-source apps that run on
your own computer, and its sign-in comes back to the app's own address on this computer. A
server install keeps using Claude.

## Signing out

**Settings › Models › Sign out** ends the sign-in at OpenAI and deletes it from the vault.
Sorting requests goes back to its Claude model.

**It says to sign in again.** OpenAI ended the sign-in: you signed out of ChatGPT
everywhere, changed your password, or it went unused for 30 days. Click **Continue with
ChatGPT** again.
