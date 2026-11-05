# Helcim

## Where to get the key

In Helcim, open **All Tools**, then **Integrations**, then **API Access Configurations**.
Choose **New API Access**, set its Access Restrictions, and create it. The restrictions
narrow what the token can do: refunds need the Admin level of transaction processing. A
[developer test account](https://devdocs.helcim.com/docs/developer-testing) has its own
token, so add one connection per account.

## What it can do

- **Read:** card and ACH transactions, card batches, customers and their saved cards (as
  tokens), invoices, recurring plans and subscribers.
- **Change:** customers and invoices.
- **Move money:** charge or hold a saved card, capture, refund, void, take an ACH payment,
  and manage subscribers.

## What's held

- **Reading runs by itself.**
- **Customers and invoices wait for your approval.**
- **Charges, captures, refunds, voids, ACH payments and subscription changes** need the
  connection's name typed, in the app.
- The approval card shows the amount, currency, customer and account in large text.

A charge uses a saved card token. Helcim's payment call can also take a full card number:
Ringkeeper refuses any call that carries one.

## Undo

A customer edit can be undone. A charge or refund can't.
