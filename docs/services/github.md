# GitHub

## Where to get the key

A [fine-grained token](https://github.com/settings/personal-access-tokens/new)
(best): Resource owner = the client's organization (or you), **Only select
repositories** (this client's), and Repository permissions **Contents, Issues,
Pull requests, Metadata** (read and write to push and open issues and pull
requests; read-only to only read). To have a routine watch its checks, add
**Checks** (read-only). Everything else at No access. Set an expiry.

A [classic token](https://github.com/settings/tokens/new) (scope `repo`) only if
the organization doesn't allow fine-grained ones. GitHub Enterprise Server:
add its address.

## What it can do

- **Read:** repositories, issues, pull requests, files, branches, commits, and the
  checks on a commit. Pull requests can be filtered by label, like issues. Each list says how many there are in all, and for issues and pull
  requests of every state, how many are open.
- **Change:** open and update issues, comment, open and merge pull requests,
  push a branch. A push never forces.

## What's held

**Every change waits for your approval.** A merge needs the connection's name
typed.

## Narrowing it

Pin each project to the repositories it needs (`owner/name`; **Add another repository**
for more than one): its tasks can't reach the token's other repositories. The gateway
checks every call, a call that leaves the repository out gets it filled in when there's only
one, and `list_repos` lists only the pinned ones.

Your own GitHub connection (one token for several clients) linked to a client's project
with no repository pinned says so: "This project could reach every repository this token
can."

## Undo

Changes on GitHub can't be undone by Ringkeeper.

## Automation watch

A routine can start when a check fails or an issue is opened, to find out why and propose a fix. See
[Automation watch](../agents-and-routines.md#automation-watch).
