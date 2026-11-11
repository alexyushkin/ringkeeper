# Working faster

Small things that save a trip.

## Quick request

Press **Ctrl+Alt+R** anywhere, and a small box opens over whatever you're doing.
Type or hold the mic, choose a client or project if you like, and send. It says
**Sent**, with a link to the conversation, and closes. Change the key or turn it off
in Settings › Advanced › Quick request. If another app already uses it, Ringkeeper
says so and you can pick another.

## Sending with the keyboard

Every box you type in works the same way: the request box on Home, the quick request,
a conversation, **Continue this task**, answers to questions and plans, and Help.
Under each box a line says which key sends.

Choose in Settings › General › Send with:

- **Ctrl+Enter** (the default): Enter starts a new line. On a Mac it's **Cmd+Enter**.
- **Enter**: Shift+Enter starts a new line.

The choice is kept by Ringkeeper, not the browser, so it's the same in every browser you
open Ringkeeper in. **Esc** leaves the box. While the @ list is open, Enter picks
from it and nothing is sent. In a plan's note, the key sends it as **Ask for changes**; it
never approves a plan or a change.

## Send later

Under the request box on Home, **Send later…** picks a day and time (this computer's
time). The request waits until then, in its conversation and in **Sent for later** on
Home, each with **Cancel**. At its time it's routed like any request.

If Ringkeeper isn't running then (the computer was asleep, say), it runs when Ringkeeper
starts, unless that's more than 6 hours late: then it doesn't run, and its conversation
says so. A time can be up to 90 days ahead.

## Mentions

Type **@** in any request box to name a client, project, connection or agent. A
mention is a firm choice: the request goes there, whatever else it says. Mention a
client with several projects and Ringkeeper asks which.

## Attach files to a request

Drop files on the request box, or use the clip. They go into an `attachments` folder
in the project its task runs in, and the task is told where they are. Up to 50 MB
each. A task can save an email's attachment or a file from Drive, OneDrive or storage
there too, to work on it, and it's then listed with the task's files.

## Run again and saved requests

On a task's page, **Run again** starts the same request as a new task.
**Save this request** keeps it on its project as a shortcut: saved requests are under
the box on Home, one click to fill it in.

## Keys already in a project

When a project has a folder, its Overview and Connections tabs look in its `.env` files
and config files for keys they know, like `DATABASE_URL`, `AIRTABLE_API_KEY`, `N8N_API_KEY`,
`VERCEL_TOKEN`, `TWILIO_*` and `GITHUB_TOKEN`. Each shows masked, with **Connect…** or
**Skip**. Nothing is added until you confirm that one, and nothing outside the
project's folder is read.

## Connection health

Every connection is tested once a day, reads only, like the Test button. Its card
shows the result and the reason it failed. If one stops working, or a key is 7 days
from expiring, your channels say so. GitHub tells Ringkeeper when its tokens expire;
for others, write the date on the card (Details › Change). **Test all** on Connectors
runs it now. Turn it off in Settings › Notifications › Connection check.

## Approvals by keyboard

On Approvals: **J** next, **K** previous, **A** approve, **D** deny. A change that
needs the connection's name typed puts you in that box instead.

**Approve all N like this** shows when one task is waiting on the same action, with
the same details, more than once.

## Trust an action for an hour

**Approve, and trust this in the project for 1 hour** lets that tool on that connection
run without asking in that project, for an hour. It's never offered for deletes or
anything else that needs the name typed, and even under a trust such a call still
asks. Approvals lists what's trusted, with **Stop trusting**. Every trust and every
call under one is in Activity.

## Morning digest

Home shows the last 24 hours per client: what ran, what failed and what waits for you.
To get it on Telegram, Slack and Discord too, turn on Settings › Notifications ›
Morning digest and set the time.

## Pins and recents

The client and project switcher, and Ctrl+K, list what you pinned and what you used
lately first. The star beside the switcher pins what's chosen.
