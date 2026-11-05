# Housecall Pro

## Where to get the key

Sign in to Housecall Pro as an admin, open My Apps, then All Apps, and find API Key Management.
Generate a key, name it, and choose full or read-only access. The API needs the MAX plan or
above. [Housecall Pro's guide](https://help.housecallpro.com/en/articles/8505035-api-overview)
has more.

## What it can do

- **Read:** the company, customers and their addresses, employees, jobs, line items,
  appointments, estimates, leads and invoices.
- **Change:** add or edit customers, add jobs, job notes and line items, move appointments, add
  leads, and turn a lead into an estimate or job.

## What's held

- **Adding and editing wait for your approval,** even on test data.
- **Changing a job's time needs the name typed,** unless the call sets both `notify` and
  `notify_pro` to false, so nobody is told.
- **An estimate with a schedule needs the name typed,** unless `notify_customer` is false.
- **Dispatching a job, approving an estimate, bulk line item edits and deleting an appointment**
  need the connection's name typed, in the app.

## Undo

An edit to a customer can be undone, and a field that was empty before stays as the edit left
it. Nothing else can be undone here.
