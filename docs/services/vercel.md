# Vercel (deployments)

## Where to get the key

An **access token** from [Vercel](https://vercel.com/account/tokens), scoped to
this client's team (or one project), with an expiry date.

## What it can do

- **Read:** teams, projects, deployments and their build logs, and the **names**
  of environment variables. Ringkeeper never reads their values.
- **Change:** redeploy, put a deployment live (promote), roll back.

## What's held

Reads always run. Changes run straight away on a staging or development connection, and wait for your approval on a production one.

## Narrowing it

A project's link can be limited to **one team**, and optionally **one project**.

## Undo

A deploy can't be undone by Ringkeeper. Put the earlier deployment live in
Vercel (or ask Ringkeeper to promote it).
