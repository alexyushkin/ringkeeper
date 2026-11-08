# Roadmap

What's being worked on, in order. Dates are targets, not promises.
[CHANGELOG.md](CHANGELOG.md) says what's already out.

## Now: provable (November 2026)

Prove what Ringkeeper claims before adding more.

- A [self-test](docs/self-test.md) that sends real requests through the real router, workers and
  gateway, with a [published report](docs/scenarios)
- Tasks in Docker on the desktop, away from the vault, the keychain and your other files
- A recorded demo you can re-create after any UI change
- What a visitor needs: issue templates, this roadmap, and good first issues

## Next: four features and some conveniences

- **Passes.** Use a project from Claude Desktop, Claude Code and Cursor, through the same gateway:
  the same rules, holds, audit and undo. Revocable, and limited to one project.
- **Automation watch.** Read-only triggers for a failed n8n run, a failed Vercel deployment, a
  failed GitHub check or new issue, and a website check. A "Fix it" agent proposes the fix, held
  like any change.
- **Client report and spend.** A month's tasks, changes, approvals, failures and AI cost per client,
  a report you can send, CSV export, and a monthly cap per client.
- **Recipes.** Export an agent and its routines as one file with no secrets. Import from a file or
  a link, see everything first, and imported routines start paused.
- **Conveniences.** One-off scheduled requests, snoozing an approval, new requests by DM on Slack
  and Discord, a model and cost range on large plans, and a forward-an-email recipe.

## After that: integrations

- **Cal.com,** through its official remote MCP server. Bookings are held because they reach people.
- **Google Docs,** on the Google connection, and **PageSpeed Insights.**
- **Make,** if its REST API works on the free plan.

## On request

Not planned yet. [Open a service request](https://github.com/alexyushkin/ringkeeper/issues/new?template=service-request.yml)
if you'd use one of these, and say what you'd ask it to do.

- A client's Slack workspace as a connection: reads run, posts are held
- Mailchimp, Klaviyo and Brevo: sending a campaign needs the name typed
- ClickUp and Asana
- WhatsApp through the existing Twilio connection: every message held
- Passes for a server install, so an AI app on another computer reaches it over HTTPS
