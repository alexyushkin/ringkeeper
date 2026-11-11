# Roadmap

What's being worked on, in order. Dates are targets, not promises.
[CHANGELOG.md](CHANGELOG.md) says what's already out.

## Now: fixes only, until launch (December 1, 2026)

No new features before launch: only fixes for what turns up in daily use.

## After launch

- **Code tasks in a worktree of their own.** Each code task gets its own git worktree, so
  several run in the same project at once and none ever touches your checkout.
- **A recipe for many projects at once.** Apply one recipe to several projects in one go.
- **A guided new-client flow.** One walk-through to add a client: its projects, its
  connections, and who approves what.

## Done in November

- **Provable.** A [self-test](docs/self-test.md) that sends real requests through the real router,
  workers and gateway, with a [published report](docs/scenarios); tasks in Docker on the desktop,
  away from the vault, the keychain and your other files; a recorded demo; issue templates.
- **Passes.** Use a project from Claude Desktop, Claude Code and Cursor through the same gateway:
  the same rules, holds, audit and undo. Revocable, and limited to one project.
- **Automation watch.** Read-only triggers for a failed n8n run, a failed Vercel deployment, a
  failed GitHub check or new issue, and a website check, with a "Fix it" agent.
- **Client report and spend.** A month per client, a report you can send, CSV export, and a
  monthly cap per client.
- **Recipes.** An agent and its routines as one file, with no secrets. Shared in
  [ringkeeper-recipes](https://github.com/alexyushkin/ringkeeper-recipes).
- **Conveniences.** Requests sent for later, snoozing an approval, new requests by DM on Slack
  and Discord, a model and cost range on large plans.
- **Integrations.** Cal.com through its official remote MCP server, Google Docs on the Google
  connection, and PageSpeed Insights.

## On request

Not planned yet. [Open a service request](https://github.com/alexyushkin/ringkeeper/issues/new?template=service-request.yml)
if you'd use one of these, and say what you'd ask it to do.

- A client's Slack workspace as a connection: reads run, posts are held
- Mailchimp, Klaviyo and Brevo: sending a campaign needs the name typed
- ClickUp and Asana
- WhatsApp through the existing Twilio connection: every message held
- Make: on the free plan only running your scenarios, through Make's own MCP server; building
  and managing them needs a paid Make plan, since its API isn't on the free plan
- Passes for a server install, so an AI app on another computer reaches it over HTTPS
- Approve and Deny on desktop notifications, once the desktop app's notifications support
  buttons on Windows and macOS (today only phones get them)
