# Wave

## Where to get the key

In [Wave's developer portal](https://developer.waveapps.com/hc/en-us/articles/50682277703188-3-Authentication),
open Manage Applications, create an application, and make a full access token for it. The
token reaches every business in your Wave account, so each call names a business.

## What it can do

- **Read:** businesses, customers, vendors, products, sales taxes, invoices, estimates,
  payments, and the chart of accounts with balances.
- **Change:** customers, products, estimates and invoices.
- **Money:** approve and send invoices, record payments and transactions.

Wave's API has no reports and no bills. Account balances are the closest it offers.

## What's held

- **No write ever runs by itself,** even on test data.
- **Draft invoices, estimates, customers and products** wait for your approval.
- **The name typed, in the app:** approving, sending, payments, money transactions, editing
  an invoice, deleting, and any invoice that doesn't say it's a draft.

An edit needs the name because the call can't show the invoice is still a draft. Wave's
approval can't be taken back.

## Undo

Nothing here can be undone by Ringkeeper. Delete a draft in Wave instead.
