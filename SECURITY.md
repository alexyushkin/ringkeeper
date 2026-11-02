# Security

Ringkeeper holds keys to your clients' systems, so security reports matter a
lot. Thank you for taking the time.

## Reporting a vulnerability

**Please don't open a public issue.** Report it privately on GitHub:
[Security › Report a vulnerability](https://github.com/alexyushkin/ringkeeper/security/advisories/new).
Only the maintainer sees it.

Include what you found, how to reproduce it, and what someone could do with
it. Never include real keys or client data; made-up ones are fine.

You'll get a reply within 3 working days. Once it's fixed and released, the
advisory is published with credit to you, unless you'd rather not be named.

## What's in scope

- The desktop app and its installer and updater
- The server (Docker) setup in this repository
- Anything that lets a task reach another client's connections, read a key
  from the vault, skip an approval, or change the audit log

Out of scope: problems that need someone already in control of your Windows
account or server, and issues in the services Ringkeeper connects to.

## Supported versions

The latest release. Updates install from inside the app.

How Ringkeeper keeps keys and data safe is described in
[docs/security.md](docs/security.md).
