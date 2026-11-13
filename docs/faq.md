# FAQ

**Is it free?** Yes: free and open source (MIT). You pay Anthropic for Claude
(your own API key, or your Claude subscription for personal use).

**Does my clients' data go to you?** No. Ringkeeper runs on your computer, with
no account and no server of ours. See [Security](security.md) for what leaves
your computer, and where it goes.

**Mac or Linux?** The desktop app is made for Windows 10 and 11. For Macs
with macOS 12 or newer, Apple Silicon or Intel, there's an experimental unsigned
build, reports welcome ([how to install it](install-desktop.md#mac-experimental)).
On Linux, the [server install](install-server.md) runs in Docker.

**Why does Windows warn about the installer?** The beta isn't code-signed yet.
Click **More info › Run anyway**.

**Why won't my Mac open it?** The Mac build isn't signed yet. The first time, in
Finder, choose **Go › Applications**, right-click **Ringkeeper**, and choose
**Open**. See [how to install it](install-desktop.md#mac-experimental).

**Where do I see what a task changed?** Open the task from Tasks: Data it changed
lists what it did on connections, with Revert, and Changes shows its files.

**Can a task run up a big bill?** Each task has a spending cap (Settings › Advanced, $1 by
default). At the cap it pauses and asks whether to go on, with a Continue button. On a Claude
subscription, which isn't billed per task, the cap is counted from the estimated cost.

**Can it change things without asking?** Live client data never changes without
asking. Reads always run. Changes on staging
and development connections run and are logged. Changes on production
connections, and anything that reaches real people (emails, texts), wait for
your approval. See [Approvals and safety](approvals.md).

**Can I undo a change?** Database updates and deletes, Airtable records and
n8n workflows: yes, for 7 days, from the task page. Sent emails, texts and
deploys: no, and the approval says so first. See [Undo](undo.md).

**What's the difference between an agent and a routine?** An agent is who does
the work (its instructions and connections); a routine is when it runs (a
schedule, an event, or Run now). See [Agents and routines](agents-and-routines.md).

**Can I approve from my phone?** Yes: Telegram, Slack or Discord. Safe changes
can be approved there; risky ones say "Open in Ringkeeper". See
[Notifications](notifications.md).

**How do I move to a new computer?** Make a backup in Settings › Backup and
restore it on the new one. See [Backup](backup.md).

**Can I use my Claude subscription instead of an API key?** For your own work, yes.
Turn on Settings › Claude › Personal subscription mode, run `claude setup-token` in a
terminal and paste the token, or use your Claude Code login. Your subscription is for
your own use: use an API key for client work.

**Where are my agents?** Agents in the menu lists every project's agents; the Agents
tab on a project lists its own. **New agent** asks for the project first.

**Is the desktop app a sandbox?** No. On the desktop, the AI runs as your Windows or Mac
user. Ringkeeper's holds and gateway control what it does through connections, but it isn't
a sandbox: a task can reach what your user can reach on this computer, including your files.
For strong isolation, use server mode, where tasks run in Docker workers. See
[Security](security.md).

**Can a task read scanned PDFs and pictures?** Yes, in the desktop app, after a one-time
download. The first time a task meets a scan it can't read, the app offers the scan reader
at the top of the window. If you choose Not now, it's in Settings › Reading scans. It reads
English, on this computer, and nothing leaves it. On a Mac it needs macOS 15 or later. A
server install doesn't read scans yet. Without the scan reader, a task can still look at a page
as a picture, which keeps a table's or a chart's layout.

**A task said it couldn't read a file, and ended as Not done. Why not Done?** Because what you
asked for isn't there. When the main thing you asked for wasn't delivered, a task ends as Not
done, even if it did part of it or suggests ways forward, so you can see at a glance that it
needs you. Its answer names the file and what would work instead, like a PDF or .xlsx copy
of a Numbers spreadsheet.

**Something's wrong. Where do I start?** [Troubleshooting](troubleshooting.md),
then Help › Report a problem in the app.

## What Ringkeeper doesn't do yet

- **Isolation without Docker on the desktop:** tasks can't reach your own files only when they run in Docker (Settings > Security). Without Docker Desktop there's no separate account for tasks yet.
- **Team accounts:** Ringkeeper is for one person; there are no teammates, shared logins or roles.
  A team can share agents' notes, each person with their own Ringkeeper:
  [Sharing memory with a team](team-memory.md).
- **A phone app:** there's no iPhone or Android app. Approve from Telegram, Slack or Discord; a server install also works in a phone's browser.
- **Other languages:** the app is in English only, with no translations such as French or Spanish. Voice input understands other languages.
- **Billing:** no invoices or time tracking. A client's month shows what their tasks cost and gives a report to send them ([A client's month](client-month.md)), but billing them is up to you.
- **Exporting everything to spreadsheets:** a client's month downloads its tasks as CSV, and the Activity page its log. There's no export of all tasks at once, and a backup is a whole copy, for restoring.
