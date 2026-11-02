# FAQ

**Is it free?** Yes: free and open source (MIT). You pay Anthropic for Claude
(your own API key, or your Claude subscription for personal use).

**Does my clients' data go to you?** No. Ringkeeper runs on your computer, with
no account and no server of ours. See [Security](security.md) for what leaves
your computer, and where it goes.

**Mac or Linux?** The desktop app is Windows 10 and 11 for now. On other
systems, the [server install](install-server.md) runs in Docker.

**Why does Windows warn about the installer?** The beta isn't code-signed yet.
Click **More info › Run anyway**.

**Can it change things without asking?** Reads always run. Changes on staging
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

**Something's wrong. Where do I start?** [Troubleshooting](troubleshooting.md),
then Help › Report a problem in the app.
