# <img src="assets/icon.svg" alt="" width="40" align="top"> Ringkeeper

[![CI](https://github.com/alexyushkin/ringkeeper/actions/workflows/ci.yml/badge.svg)](https://github.com/alexyushkin/ringkeeper/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/alexyushkin/ringkeeper-releases?include_prereleases&label=release)](https://github.com/alexyushkin/ringkeeper-releases/releases/latest)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Ringkeeper lets one person run AI agents across many clients without ever
switching connections.

If you work for many clients, each has its own database, Airtable, n8n, Vercel
and email. In Ringkeeper you connect each client's tools once, attach them to
that client's projects, and just ask: "Add last week's leads to Acme's
Airtable." It works out which client and project you mean, uses only that
project's connections, and anything that changes live data waits for your OK.

**[Download for Windows](https://github.com/alexyushkin/ringkeeper-releases/releases/latest)**
· Windows 10/11 · free and open source (MIT) · beta: SmartScreen will warn,
click **More info › Run anyway**.

| Ask in plain words | Approve what matters | Undo for 7 days |
| --- | --- | --- |
| ![Home, with the Get started checklist and requests to try](docs/images/home.png) | ![A change to a production database waiting for approval](docs/images/approval.png) | ![A task's change with Revert](docs/images/undo.png) |

- Connect once per client; one client's connections can never reach another's
- Changes to live data wait for your approval, and can be reverted for 7 days
- Runs on your computer with your own Claude: no account, no Ringkeeper server
- Agents and routines: named helpers that run on a schedule or when something happens
- Approve from Telegram, Slack or Discord; back up and move to a new computer

**Learn more:** [User guide](docs/README.md) · [Concepts](docs/concepts.md) ·
[Install on Windows](docs/install-desktop.md) · [Server (Docker)](docs/install-server.md) ·
[Security](docs/security.md) · [FAQ](docs/faq.md) ·
[For developers](docs/developers.md) · [Contributing](CONTRIBUTING.md) ·
[Changelog](CHANGELOG.md)

Need help setting it up for your clients? <https://yushkin.us> or
[LinkedIn](https://www.linkedin.com/in/alexey-yushkin).

License: [MIT](LICENSE).
