# Any API

For a service with an HTTP API and no connection of its own. Give its address and how it
signs in: a bearer token, a key in a header, a username and password, or a key in the
address. If the API publishes an OpenAPI file, add its address too, and the AI can see
what the API offers.

**Forms On Fire** and **DataForSEO** have their own tiles, with the address filled in.
The Forms On Fire tile is marked **Unverified**: its address and sign-in haven't been
confirmed against Forms On Fire's own documentation, so check both in your account. Other
regions and Appenate use their own.

## What it can do

- **Read:** GET and HEAD anything under the address, and list its endpoints.
- **Change:** POST, PUT, PATCH and DELETE.

A call can't leave the address: paths can't climb out of it, and redirects aren't
followed, so the key is never carried somewhere else.

## What's held

- **GET and HEAD run.**
- **POST, PUT and PATCH** wait for your approval, even on test data: an API can do
  anything with a POST, and DataForSEO's cost money.
- **DELETE** needs the connection's name typed, in the app.

A project's link can be limited to a path, like `/v1/forms`.

## Undo

Ringkeeper can't undo what an API call does.
