# GitHub

## Where to get the key

A [fine-grained token](https://github.com/settings/personal-access-tokens/new)
(best): Resource owner = the client's organization (or you), **Only select
repositories** (this client's), and Repository permissions **Contents, Issues,
Pull requests, Metadata** (read and write to push and open issues and pull
requests; read-only to only read). Everything else at No access. Set an expiry.

A [classic token](https://github.com/settings/tokens/new) (scope `repo`) only if
the organization doesn't allow fine-grained ones. GitHub Enterprise Server:
add its address.

## What it can do

- **Read:** repositories, issues, pull requests, files, branches, commits.
- **Change:** open and update issues, comment, open and merge pull requests,
  push a branch. A push never forces.

## What's held

**Every change waits for your approval.** A merge needs the connection's name
typed.

## Narrowing it

Pin each project to **one repository** (`owner/name`): its tasks can't reach
the token's other repositories.

## Undo

Changes on GitHub can't be undone by Ringkeeper.
