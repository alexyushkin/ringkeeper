# Shopify

## Where to get the key

In the Shopify admin, open **Apps**, then **Develop apps**, pick the store's custom app, and
copy its Admin API access token. Give the app only the access it needs. Then type the store
address, like `acme.myshopify.com`. One token reaches one store, so add one connection per
store.

Only custom apps made before January 2026 have a token to paste. Newer apps are made in
Shopify's Dev Dashboard and use short-lived tokens, which this connection can't get yet.

## What it can do

- **Read:** the store, products, orders, customers, draft orders, collections, pages, blog
  articles, themes and sales channels.
- **Change:** draft products, customers, order notes, draft orders, and draft pages and
  articles.
- **Go live:** publish or activate, change prices in bulk, refund, cancel or fulfil an order,
  and change themes and store policies.

## What's held

- **Reading runs by itself,** orders and customers too.
- **A product created as a draft waits for your approval.** So does a customer, a draft
  order, or a page or article created as unpublished.
- **Publishing, activating, or changing a status** needs the connection's name typed, in the
  app. A new product only counts as a draft if the call says `DRAFT`.
- **Bulk price changes, refunds, cancelling or fulfilling an order, deleting, themes and
  store policies** need the name typed too.
- An edit to something already live, with no change of status, only waits for approval. Check
  the card before you approve it.

## Undo

Nothing here can be undone yet. A refund or a cancelled order can't be taken back.
