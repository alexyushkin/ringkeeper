# Working faster

Small things that save a trip.

## Quick request

Press **Ctrl+Alt+R** anywhere, and a small box opens over whatever you're doing.
Type or hold the mic, choose a client or project if you like, and send. It says
**Sent**, with a link to the conversation, and closes. Change the key or turn it off
in Settings › Advanced › Quick request. If another app already uses it, Ringkeeper
says so and you can pick another.

## Mentions

Type **@** in any request box to name a client, project, connection or agent. A
mention is a firm choice: the request goes there, whatever else it says. Mention a
client with several projects and Ringkeeper asks which.

## Attach files to a request

Drop files on the request box, or use the clip. They go into an `attachments` folder
in the project its task runs in, and the task is told where they are. Up to 50 MB
each.

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
