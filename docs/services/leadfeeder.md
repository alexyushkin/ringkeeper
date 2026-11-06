# Leadfeeder

Leadfeeder (by Dealfront) shows which companies visit a website. It connects through Leadfeeder's
own MCP server. You can add it for a client, or as your own shared tool.

**It needs a paid Leadfeeder plan.** On the free plan only sign-in and the account list work:
everything else comes back refused. The connection's card says so, and tasks that hit it stop
there instead of trying again. After upgrading, press **Test**.

## Where to get the key

There's no key to paste: Leadfeeder's server takes a sign-in only. The API key in your Personal
settings is for Leadfeeder's other API, so Ringkeeper leaves one in a project's `.env` file alone.

1. Add **Leadfeeder** under Connections and sign in. Your account needs API and MCP access, and your
   user needs the **Manage API keys and use API** permission. In a company workspace, an admin may
   have to approve the connection.
2. Press **Test**. It lists the Leadfeeder accounts this sign-in reaches, with their IDs.
3. On each project's link, set the **Leadfeeder account** it may use. Each account tracks one
   website. A project can't use Leadfeeder until that's set.

## What it can do

- **Read:** website visits, companies, contacts, lists, tags, custom fields, and how many credits a
  job would cost.
- **Change:** tags, lists, custom fields, feeds and goals.
- **Spend credits:** open a company's or contact's full details, find contact data, and enrich
  companies.

## What's held

- **Reading runs by itself.**
- **Changes wait for your approval.** That covers tagging, adding to lists, and making lists, tags
  and custom fields.
- **Anything that may spend credits waits, and the card shows the cost.** Jobs show Leadfeeder's own
  estimate, like `About 120 credits`. Opening records shows the most it can cost, like `Up to 3
  credits`: a record you opened in the last 12 months is free. If Leadfeeder can't estimate a job,
  the card says `Credits not known`.
- **Deletes need the connection's name typed,** in the app itself. So do the cookie consent and data
  processing settings.
- **Nothing here can be trusted for an hour,** and none of it can be approved from a chat app when
  it needs the name typed.

Leadfeeder asks for its own confirmation on these calls. Your approval in Ringkeeper answers it, so
you only approve once, and an agent can't confirm anything itself.

## Undo

Nothing here can be undone by Ringkeeper. Credits spent stay spent.
