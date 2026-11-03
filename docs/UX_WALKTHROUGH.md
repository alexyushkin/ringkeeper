# UX walkthrough

Six journeys through the desktop app, walked in a scratch install (its own data
folder and keychain name), fixed, and walked again from a fresh install on the
final build. The screenshots are from that last walk, except the ones marked
"before".

## How it was walked

The real app, with its real pages, gateway, approvals, undo, backup and the real
Claude Code CLI as the worker. Three things stood in for what a scratch install
can't have:

| Stand-in | Why | What it means |
|---|---|---|
| A scripted model on 127.0.0.1 | No key or subscription of yours was used. The token pasted at first run was a made-up one. | Routing and tool calls ran through the real router, worker and gateway, but the "AI" follows a script. How well a real model picks tools or words its replies was **not** tested. |
| A small n8n API on 127.0.0.1 | No client's n8n | Real adapter, real key check, fake workflows. That's why the n8n connection reads `n8n · 127.0.0.1`. |
| Google's HTTP answered by a fake (the seam the tests use) | No Google project or accounts | Real sign-in callback, real account storage, real gateway. **Google's own sign-in page and warning screen were not seen.** |

Not walked: the service sign-ins (Notion, Neon and so on) past their form, which
need real accounts, and the installer, which stays on the clean CI runner.

## a) New user: first run → demo data → first task → approve → undo

| | |
|---|---|
| ![First run before](ux-walkthrough/before-first-run.png) | ![First run now](ux-walkthrough/a01-first-run-claude.png) |
| Before: API key first, even with subscription mode on | Now: your subscription first, setup-token first |

Then **Try it with demo data**, and a first request. Anything that would use live
data asks first, in the conversation:

![Go ahead](ux-walkthrough/a05-go-ahead.png)

The change itself waits in Approvals, with the SQL and whether it can be undone:

![Approval](ux-walkthrough/a07-approvals.png)

After it ran, the task shows what it changed and offers **Revert**, which also
waits for approval:

![Task done](ux-walkthrough/a08-task-done.png)
![Reverted](ux-walkthrough/a10-reverted.png)

**Where I hesitated**

- First run defaulted to an API key with subscription mode on. Fixed.
- The "Go ahead?" card and the approval said `demo-shop-db`. Now "Demo shop database".
- The transcript said `mcp__ringkeeper-database__execute`. Now "Database: execute".
- A task's result was only in its transcript. Its last message now shows on its card.

## b) A client, a project, their n8n, linked, and a task that uses it

**From Claude** is the fastest way to a project. Most recent first, grouped by likely
client, with a search once the list is long. A Claude project named like a folder
shows once ("also a Claude project"), and a folder that only holds others is folded
away:

![From Claude](ux-walkthrough/b02-from-claude.png)

| | |
|---|---|
| ![Before](ux-walkthrough/before-add-connection.png) | ![Now](ux-walkthrough/b05-connectors-tiles.png) |
| Before: n8n hidden under "Other services" at the bottom | Now: built-in tiles, then sign-in services, then search |

| | |
|---|---|
| ![Before](ux-walkthrough/before-search-n8n.png) | ![Now](ux-walkthrough/b06-search-n8n.png) |
| Before: ten look-alikes, no publishers | Now: the built-in tile on top, community servers folded away with a warning |

The form asks **Is this live client data?** with nothing chosen, and says what each
answer lets run. Its name is filled in from the address:

![n8n form](ux-walkthrough/b07-n8n-asks-live-or-test.png)

| | |
|---|---|
| ![Before](ux-walkthrough/before-client-connections.png) | ![Now](ux-walkthrough/b09-client-connections.png) |
| Before: linking was inside a collapsed row | Now: where it's used and Link to project… on the card |

The overview says what's linked, and a project's tab has **Link a connection** at the top:

![Connectors](ux-walkthrough/b10-connectors-page.png)
![Link a connection](ux-walkthrough/b11-project-link-a-connection.png)

A read ran straight away; activating a workflow on the production n8n waited:

![n8n approval](ux-walkthrough/b14-approval.png)

**Where I hesitated**

- After adding a project for a new client, the page showed `general-informatics-llc`. Fixed.
- The header read "1 project · 1 · 1 production connection". Now "1 project · 1 connection (1 production)".
- After linking from the success card, the card above it still said "Not linked". Fixed.

## c) Your own tool (Playwright), in all projects except one client

Adding your own tool searches first, and doesn't ask about client data, because it
holds none. The official server is on top:

![Search](ux-walkthrough/c01-search-playwright.png)
![All except Demo Co](ux-walkthrough/c04-all-except-demo-co.png)
![The project has it](ux-walkthrough/c05-project-has-it.png)

Checked: `geninfos-website` has it, `demo-shop` (Demo Co) doesn't.

**Where I hesitated**

- Microsoft publishes Playwright's server from a GitHub account, so it fell under community servers. A short list of known official publishers fixes that.
- Official servers can be titled just "mcp" (stripe.com's). They now read as "Stripe".
- The search sat below tiles you don't need for this. It now comes first here.
- "Is this live client data?" on a tool that holds none. Not asked any more.
- The card showed `io.github.microsoft/playwright-mcp` and a "Test" badge. Both gone.
- An open group's label turned into "Hide details". It keeps its name now.

## d) Two Google accounts for two clients, the right one sends a draft

Settings › Google app is the one-time setup, with **Add a Google account** and each
account's client and projects right there:

![Google settings](ux-walkthrough/d05-settings-google-accounts.png)
![Add an account](ux-walkthrough/d03-google-account-form.png)

A draft for `seaside-newsletter` asks first, names the account, and went out as that
account (the fake Google logged `POST /drafts as office@seasidewindows.example`):

![Draft approval](ux-walkthrough/d07-draft-approval.png)

**Where I hesitated**

- A new account couldn't write drafts: the default access left it out, and the task ended with "wasn't given Write drafts". Drafts are on by default now (every write still asks first).
- The task failed with `Filename too long` in a deep folder (`git switch` at the Windows path limit). Git's long paths are on for task branches now.
- The card said "Live change" for a draft. That label is for production now; this one says "Change".
- Add a Google account takes three steps from Settings: whose it is, which client, then the form. It's clear, but it's one more than I'd like.

## e) An agent and a routine: test run, result and approval inline

Both pages explain the pair in a line and have a New button that asks for the project
first. With no agent yet, a routine offers one right there:

![Create an agent](ux-walkthrough/e05-routine-offers-agent.png)

The test run's approval shows on the routine's page, and so does its result:

![Approval inline](ux-walkthrough/e08-routine-approval-inline.png)
![Result inline](ux-walkthrough/e09-routine-result.png)

**Where I hesitated**

- The run showed "Done" but no result, only a link to the task. It shows the result now.
- A project's own New routine said "add an agent first" with no button. It now offers Create an agent.
- With no projects, the project list was an empty dropdown. It now offers Add project and From Claude.

## f) Back up, restore on a fresh data folder

![Backup](ux-walkthrough/f02-backup-made.png)

On a brand-new install: **Restore a backup instead** at first run, then the backup's
password. It shows what's inside before anything is replaced:

![Preview](ux-walkthrough/f04-restore-preview.png)

After the restart the clients, projects and connections were back, and the n8n test
passed with its restored key:

![Restored](ux-walkthrough/f06-restored-connectors.png)

No hesitation here. Without the desktop shell the app has to be started again by
hand after a restore, so the shell's own restart wasn't part of this walk.

## Screen sizes

Every page, at 1366×768 and 375 wide, with data in it: 30 of 30 with nothing wider
than the screen and no scrollbars on tab strips. `/tasks` was 77 px too wide on a
phone because of an unbroken error line; fixed.

![Phone](ux-walkthrough/g02-connectors-375.png)

Other connectors now waits for a search, instead of listing dozens of unrelated
servers before you type.

## The list, item by item

| Item | State |
|---|---|
| Built-in tiles, sign-in, then search | Done |
| Tiles "with logos" | **Partly.** Each service has its initials on its color. Real logos are a licensing and trademark choice, so that's yours to make. |
| Connectors name, Browse connectors, Claude note | Done |
| Search: publisher, badge, dedupe, ranking, community folded | Done |
| No default for kind of data | Done (client connections; your own MCP tools aren't asked) |
| "Use in all projects" only for your own tools | Done |
| Readable connection names | Done; existing connections are renamed by the migration |
| Linking from the client, the project, the overview | Done |
| No projects: say so, offer Add project and From Claude | Done, on the client page, the success card, agents and routines |
| From Claude cleanup | Wasn't done; done now |
| Archive failed tasks older than 7 days | Done. It covers failed, timed-out, over-budget and stopped tasks. It only shows when there are some. The button was unit-tested but not clicked in this walk, because a fresh install has no week-old tasks. |
| Subscription first; tasks, architect, routines and router use it | Done. Router, tasks and routines ran on a subscription token here; the architect shares the workers' path, checked in code, not walked. |
| Settings › Google | Done |
| Test failures show the reason on the card | Done |
| Agents and Routines: New, project first, Create an agent | Done |
| No stray scrollbars; 1366×768 and 375 | Done |
| Readable **project** names | **Not done.** Connections and clients read well; projects still show their short name (`geninfos-website`), which is usually readable but isn't what was typed. |
