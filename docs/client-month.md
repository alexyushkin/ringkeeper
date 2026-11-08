# A client's month

Each client's page has a **This month** tab: what Ringkeeper did for them in a month, and what
it cost. Pick another month at the top.

It counts only that client's projects, so one client's month never shows another's work:

- **Tasks** started that month, however they started (from the app, a routine, Telegram, Slack
  or a pass), and how each ended.
- **Changes** made to their services, and the ones checked before they ran: how many you
  approved, declined, or left to expire.
- **Refused calls:** what Ringkeeper's checks stopped.
- **AI cost:** what Claude cost for those tasks. With a Claude subscription it's an estimate,
  since a subscription isn't billed per task.

Clients & projects shows each client's cost so far this month beside their name.

## The report

Under the month, **Download PDF** or **Download Markdown** gives a report you can send the
client: a summary and each task with its result. Tick **Include the AI cost** to add what it
cost. **Download tasks as CSV** gives the month's tasks for a spreadsheet.

The report has the tasks' titles and results with secrets taken out. It never names another
client.

### Email it

If one of the client's projects has a mail connection (SMTP, Resend, a mailbox or Gmail), type
the client's address and click **Draft the email**. The email waits in **Approvals**, like
every email Ringkeeper sends: nothing goes out until you approve it there. It goes from a mail
connection the client's projects may use: theirs, or your own mail linked to one of their
projects, never another client's.

## A monthly cap

On the client's page, Settings > Monthly cap sets the most their tasks may cost in a month, in
dollars. Once that month's AI cost reaches it:

- New tasks for the client are refused, with the reason, wherever they come from.
- A routine's run is skipped, and its history says why.
- A task that's already running finishes. Its own spending cap still applies.

The cap starts again on the 1st. To go on this month, raise it or clear it. Changes to the cap
are in Activity.

The month follows this computer's calendar (the server's, for a server install).
