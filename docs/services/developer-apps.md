# Developer apps

Some services only let an app you registered yourself sign in: Google, Microsoft (Entra),
HubSpot, Salesforce, Xero, Intuit and Jobber. You register a free app with the service
once, paste its client ID and secret into **Settings › Developer apps**, and then sign in
as many accounts through it as you need. Each account is its own connection, a client's or
your own, with the same rules as any other.

The secret stays in the vault and never comes back to the page.

## Set one up

Each service in **Settings › Developer apps** has its own steps, the address to paste, the
scopes to add, and a link to the right page. When an app is saved, **Check it** asks the
service whether it accepts the client ID and secret. Nothing is created by that check.

## More than one app

A service can have several apps, for example one registered in a client's own tenant, when
their admin only trusts that one. When a service has more than one, its sign-in asks which
to use. Removing an app stops its accounts working at their next refresh.

## Where the sign-in comes back

Most services want one exact address, and Ringkeeper's own port changes each launch, so
they come back to a fixed address on this computer: `http://localhost:17845/oauth/callback`.
It only answers on this computer. Nothing is opened to the internet.

**Intuit's production keys are different.** Intuit only sends production sign-ins to an
https address that isn't this computer. So the sign-in ends on Intuit's page, and you copy
that page's whole address back into Ringkeeper. Intuit's development keys come back here as
usual, and reach sandbox companies only.

## Good to know

- **Salesforce:** an app made in Setup works in the org it was made in. For a client's org,
  make one there and add it as another app.
- **Microsoft:** a client's admin may have to allow your app in their tenant first.
- **Xero:** apps made since March 2026 can only use the narrower scopes listed here.
- **Intuit:** production keys need Intuit's app assessment first.
