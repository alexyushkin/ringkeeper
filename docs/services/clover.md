# Clover

## Where to get the key

In the Clover Merchant Dashboard, open **Settings**, **View all settings**, then
[**Ecommerce API Tokens**](https://docs.clover.com/dev/docs/create-ecommerce-api-tokens).
Create a token for an API integration and copy the private token. It belongs to one merchant.
Then type the address: `scl.clover.com` for real data, or `scl-sandbox.dev.clover.com` for
the sandbox. Clover's Ecommerce API serves the US and Canada.

## What it can do

- **Read:** charges, refunds and online orders.
- **Change:** orders, and customers saved with a card token.
- **Move money:** refund a charge or return an order.

New charges aren't available here yet. Clover needs the customer's own internet address on
each one, and Ringkeeper can't send it yet. Recurring plans aren't here either: they live at
a different Clover address.

## What's held

- **Reading runs by itself.**
- **Orders and customers wait for your approval.**
- **Refunds and returns** need the connection's name typed, in the app, and so does removing
  a saved card.
- The approval card shows the amount and the charge in large text.

## Undo

Nothing here can be undone. A refund can't be taken back.
