# Ghost

## Where to get the key

In Ghost Admin, open **Settings**, find **Integrations**, and add a custom integration. Copy
its Admin API key, which looks like `id:secret`. Then type the domain you open Ghost Admin
on, like `acme.ghost.io`. One key reaches one site.

## What it can do

- **Read:** the site, posts, pages, tags, newsletters, tiers, offers and members.
- **Change:** draft posts and pages, tags and members.
- **Go live:** publish, schedule or unpublish, send a newsletter, delete, and change the
  active theme.

## What's held

- **Reading runs by itself.**
- **A post or page created as a draft waits for your approval,** and so do tags and members.
  Only a call that says `draft` counts as a draft.
- **Publishing, scheduling, unpublishing and sending a newsletter** need the connection's
  name typed, in the app. So does **deleting** and **changing the theme**.
- An edit to a live post, with no change of status, only waits for approval. Check the card
  before you approve it.

Ghost's settings are left out, because reading them can answer with keys and writing them can
change keys. So are staff users, integrations and webhooks.

## Undo

Nothing here can be undone yet. A sent newsletter can't be unsent.
