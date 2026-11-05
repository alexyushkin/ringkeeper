# Braintree

## Where to get the key

In the Braintree Control Panel, open your
[API settings](https://developer.paypal.com/braintree/articles/control-panel/important-gateway-credentials)
and copy a public and private key. Then type the address: `payments.braintree-api.com` for
real data, or `payments.sandbox.braintree-api.com` for the sandbox. Sandbox and production
have different keys, so add one connection per environment.

## What it can do

- **Read:** transactions, refunds, customers and disputes.
- **Change:** customers.
- **Move money:** charge or hold a saved payment method, capture, refund, void, and answer a
  dispute.

## What's held

- **Reading runs by itself.**
- **Customers wait for your approval.**
- **Charges, captures, refunds, voids and dispute answers** need the connection's name typed,
  in the app, and so does deleting a customer or a saved payment method.
- The approval card shows the amount, customer and account in large text. Braintree charges
  in the merchant account's currency, which the call doesn't state.

Anything that takes card details or hands out a client token is left out: a charge uses a
payment method already saved in Braintree.

## Undo

Nothing here can be undone. A charge or refund can't be taken back.
