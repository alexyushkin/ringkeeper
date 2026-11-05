# Square

## Where to get the key

In the [Square Developer Console](https://developer.squareup.com/apps), open your
application and choose **Credentials**. Pick Production or Sandbox at the top and copy the
access token. Then type that environment's address: `connect.squareup.com` for real data,
or `connect.squareupsandbox.com` for the sandbox. Each has its own token, so add one
connection per environment.

## What it can do

- **Read:** the account and its locations, payments, refunds, payouts, customers, orders,
  invoices, payment links, the catalog, subscriptions and disputes.
- **Change:** customers, orders, draft invoices and payment links.
- **Move money:** charge a saved source, capture or void a payment, refund, send an invoice,
  change a subscription, and answer a dispute.

## What's held

- **Reading runs by itself.**
- **Customers, orders, draft invoices and new payment links wait for your approval.**
- **Charges, captures, voids, refunds and subscription changes** need the connection's name
  typed, in the app. So does **sending an invoice**, because it can charge a saved card.
- **Accepting a dispute or adding evidence** needs the name typed too, and so does anything
  that deletes.
- The approval card shows the amount, currency, customer and account in large text. A
  capture or void names the payment, because Square's call doesn't state an amount.

Card and bank account numbers never pass through Ringkeeper: a charge uses a token
from Square's own payment form.

## Undo

A customer edit can be undone. A charge, refund or sent invoice can't.
