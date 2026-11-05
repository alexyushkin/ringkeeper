# BILL

## Where to get the key

[Sign up for a BILL developer account](https://developer.bill.com/docs/api-sandbox-sign-up),
in the sandbox or in production, and generate a developer key. You'll need four things: the
username, password, organization ID (it starts with `008`) and developer key. Then type the
address: `gateway.prod.bill.com` for real data, or `gateway.stage.bill.com` for the sandbox.
Add one connection per organization and environment.

Ringkeeper signs in with these and gets a short session, so use a BILL user made for this,
with only the access it needs.

**To pay bills,** BILL also needs its MFA ID and device name. They come from BILL's MFA
sign-in, which sends a code to your phone, and they last 30 days. Ringkeeper doesn't run that
step, so a connection without them can read and draft but can't pay.

## What it can do

- **Read:** bills, vendor payments, payment options, vendors, vendor credits, invoices,
  payments received and customers.
- **Change:** bills, invoices, customers and vendors, and make an invoice's payment link.
- **Move money:** pay a bill, cancel or void a payment, charge a customer's saved bank
  account, and send an invoice.

## What's held

- **Reading runs by itself.**
- **Bills, invoices, payment links, customers and vendors wait for your approval,** every
  time. Nothing here can be trusted for an hour.
- **Paying a bill, charging a customer, cancelling or voiding a payment and recording a
  payment** need the connection's name typed, in the app. So does **sending an invoice** and
  **archiving** anything.
- The approval card shows the amount, the vendor or customer and the account in large text. A
  cancel or void names the payment, because BILL's call doesn't state an amount.

Bank accounts are left out, for vendors, customers and your own organization, and so are
users, bulk payments and bill approvals.

## Undo

A customer or vendor edit can be undone. A payment or a sent invoice can't.
