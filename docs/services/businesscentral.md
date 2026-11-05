# Dynamics 365 Business Central

## How it signs in

Add your Microsoft app in **Settings › Developer apps**, with the Business Central API
permission, then sign in from the tile and give the environment's name, like Production or
Sandbox. One connection is one environment, and a call names the company.

## What it can do

- **Read:** trial balance, balance sheet, income statement, cash flow, aged receivables and
  payables, customers, vendors, accounts, ledger entries, invoices, payments and journals.
- **Change:** draft sales and purchase invoices and their lines.
- **Money:** post, send and cancel invoices, add customer payments and journal lines, post a
  journal.

## What's held

- **No write ever runs by itself,** even in a sandbox.
- **Draft invoices** wait for your approval. A draft isn't on the books.
- **The name typed, in the app:** posting, sending, canceling, deleting, payments and journal
  lines.

## Undo

An edit to a draft invoice's dates and details can be undone. Nothing else can: a posted
invoice is canceled in Business Central, with a credit memo.
