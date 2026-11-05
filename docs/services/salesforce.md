# Salesforce

## Where to get the key

Salesforce has no pasted key here. Make an External Client App in your org, add it in
Developer apps, then sign in from the tile. The connection remembers your org's address, like
`acme.my.salesforce.com`, and sends its token nowhere else.

## What it can do

- **Read:** SOQL queries, SOSL searches, org limits, object fields, and single contacts,
  accounts, opportunities, leads and tasks. A query reaches whatever the signed-in user can see.
- **Change:** add or edit contacts, accounts, opportunities, leads and tasks, and add notes.
- **Reach people:** send an email.

Nothing here can run Apex or a batch of arbitrary requests.

## What's held

- **Adding and editing wait for your approval,** even on test data.
- **Deletes and sending email** need the connection's name typed, in the app.

## Undo

An edit to a contact, account, opportunity, lead or task can be undone. It puts back the
standard fields, and a field that was empty before stays as the edit left it. Custom fields
aren't put back.
