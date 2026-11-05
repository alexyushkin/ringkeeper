# ServiceTitan

You can't connect ServiceTitan yet. Its calls need a token traded for a client ID and secret,
and Ringkeeper can't do that trade yet. The rules below are ready for when it can.

## Where to get the key

In the [ServiceTitan Developer Portal](https://developer.servicetitan.io/), register an app and
connect it to your account. You'll need its Client ID, client secret and application key, and
your tenant ID.

## What it can do

- **Read:** business units, technicians, customers, locations, leads, jobs, appointments,
  estimates, invoices and payments.
- **Change:** add or edit customers, leads and jobs, edit locations, add notes, move
  appointments, and add estimates.

An edit can't deactivate a customer or change their credit and payment terms. Invoices and
payments are read-only.

## What's held

- **Adding and editing wait for your approval,** even on test data.
- **Cancelling a job, deleting an appointment and marking an estimate sold** need the
  connection's name typed, in the app.

## Undo

An edit to a customer, location, lead or job can be undone. It puts back the main fields, and a
field that was empty before stays as the edit left it. An address isn't put back.
