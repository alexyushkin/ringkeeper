# Marketing, outreach, work and social services

29 services that sign in with their own official server, added in one go. Each tile says
**Beta**: its sign-in has been checked to accept Ringkeeper, but no one has tried it with a
real account yet. If one doesn't behave as described here, say so in Help › Report a problem.

Under **Connectors › Browse connectors**, choose the service and sign in with the client's
account on the service's own page. A few also take a pasted key instead (Brevo, Fireflies,
Semrush), and Instantly takes only a key. Connect one account per client.

## What waits for you

Reading runs. What else each kind needs:

| Kind | Services | Waits for approval | Needs the name typed |
| --- | --- | --- | --- |
| Email marketing | Klaviyo, Brevo, Kit, MailerLite, Customer.io | Drafts and other changes | Sending, scheduling, launching, deleting, imports |
| Outreach | Instantly, lemlist, Hunter | Adding leads and other changes | Starting a campaign or sequence, deleting |
| Social | Buffer, Hootsuite, Metricool | Other changes | Making, changing, scheduling or publishing a post, deleting |
| Ads | TikTok Ads | Creating campaigns and other changes | Budgets, launching, publishing, deleting |
| Scheduling | Calendly | Every booking, reschedule or cancel, every time | Deleting |
| Voice | Vapi | Every call and every change, every time | Deleting |
| Support | Intercom | Changes | Sending a message, deleting |
| Everything else | Make, Zapier, Trello, Basecamp, Todoist, Canva, Typeform, Jotform, Tally, Fireflies, Granola, Otter, Ahrefs, Semrush | Every change | — |

The per-tool setting on a connection can make any of these stricter, not looser.

## Costs, plans and regions

- **Costs.** Ahrefs and Semrush use the account's API units on every lookup (Ahrefs needs the
  Lite plan or higher). Hunter, lemlist and Instantly use credits to find and check addresses.
  Zapier actions use Zapier tasks and Make scenarios use Make operations. Vapi's calls are billed.
- **Plans and admins.** Kit needs a paid plan. Customer.io does nothing until an admin turns on
  MCP under Account settings › AI, and starts with drafts only. On a Granola Enterprise
  workspace an admin turns on MCP access first.
- **Europe.** Intercom and Customer.io workspaces hosted in Europe have their own tiles:
  **Intercom (Europe)** and **Customer.io (Europe)**.
- **Calendly** signs in back to `localhost` in the desktop app, because it takes no other plain
  address. Nothing to do: the desktop app answers there too.

## Not included

- **Similarweb's** server lets apps register only with a secure web address, so it can't sign
  in from the desktop app. Use its API with **Any API** instead.
- Services Ringkeeper already connects with its own built-in rules keep those: n8n, Google,
  Microsoft, Twilio, Pipedrive, Resend and DataForSEO.

Ringkeeper can't undo anything on these services, and the approval card says so.
