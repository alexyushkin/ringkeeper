# Plaid

Not available yet: the table is written, and it needs a change in Ringkeeper first.

## Where to get the key

In the [Plaid Dashboard](https://dashboard.plaid.com/developers/keys), open **Developers**,
then **Keys**, and copy the client ID and the secret for your environment. Then paste the
access token your own Plaid integration holds for one linked bank. Ringkeeper doesn't link
banks, so one connection is one bank you've already linked.

## What it can do

- **Read:** the bank's accounts, live balances, transactions, recurring payments, the
  account owners on file, investment holdings and transactions, and which statements exist.

## What's held

Nothing is held, because nothing can change. Every operation only reads.

There's no way to move money here: transfers and payments are left out entirely. Full account
and routing numbers are left out too, so account details stop at the last few digits.

## Undo

Nothing changes, so there's nothing to undo.
