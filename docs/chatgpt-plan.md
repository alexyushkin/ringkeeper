# Your ChatGPT plan

In the desktop app, Ringkeeper can use your ChatGPT plan instead of Claude: for **tasks**, for
**sorting requests**, and for **the Help assistant**. It signs in with OpenAI's **Sign in with
ChatGPT**: no API key, and nothing to pay on top of your plan.

## Turning it on

1. Open **Settings › Models** and click **Continue with ChatGPT**.
2. Your browser opens OpenAI's page. Sign in and allow Ringkeeper to use your plan.
3. Back in Settings, choose a ChatGPT model where you want it: **Sorting requests**, or the
   task sizes (**Small**, **Normal** and **Big tasks**, and **Tasks without a size**). The
   lists show the models your plan offers, marked **ChatGPT plan**.

Ringkeeper never sees your ChatGPT chats. Its sign-in is kept in Ringkeeper's vault, like a
key, and is refreshed every hour.

## Tasks on your plan

A task on a ChatGPT model runs on **Codex**, OpenAI's agent, instead of Claude Code. Everything
around it works the same way:

- It reaches only its project's connections, through the same gateway, so the same
  approvals, undo, audit log and same-client rule apply.
- It can ask you a question, say it couldn't do something, and go on after an approval.
- The project's autonomy and web access decide its shell, its edits and its web search, as
  they do for Claude.

What's different:

- **The first task downloads Codex** (about 100 MB), once. Later tasks reuse it.
- **No spending cap per task.** Your plan isn't billed by the token, so there's nothing to cap.
  The step limit still applies, and your plan's own limits.
- **Planning features stays on Claude.** It plans with Claude Code's own tools.
- **A task that changes engines starts over.** If a task moves from a Claude model to a
  ChatGPT one (a bigger size after failed checks, say), the new session starts fresh with the
  task's request, not where the other left off.

## Usage and limits

What Ringkeeper uses comes out of your plan's own limits, the same ones ChatGPT and Codex
use. In ChatGPT's settings you can also set a limit for Ringkeeper alone: click **Manage
usage** next to the model, or open chatgpt.com/settings/usage.

When the limit is reached, nothing moves to other billing. A request waits and Ringkeeper
sorts it again later; a task stops and says so, and you can send a follow-up once the limit
resets. Claude's work isn't paused by it.

**Is it for any plan?** OpenAI offers it to eligible ChatGPT plans. If your account or
workspace can't use its plan in other apps, Ringkeeper says so: choose a Claude model instead.

**Why only in the desktop app?** OpenAI offers plan usage to open-source apps that run on
your own computer, and its sign-in comes back to the app's own address on this computer. A
server install keeps using Claude.

## Signing out

**Settings › Models › Sign out** ends the sign-in at OpenAI and deletes it from the vault.
Everything set to a ChatGPT model goes back to its Claude model.

**It says to sign in again.** OpenAI ended the sign-in: you signed out of ChatGPT
everywhere, changed your password, or it went unused for 30 days. Click **Continue with
ChatGPT** again.
