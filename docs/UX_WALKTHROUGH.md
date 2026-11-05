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

Settings › Developer apps is the one-time setup, with **Add a Google account** and each
account's client and projects right there:

![Google settings](ux-walkthrough/d05-settings-google-accounts.png)
![Add an account](ux-walkthrough/d03-google-account-form.png)

A draft for `acme-newsletter` asks first, names the account, and went out as that
account (the fake Google logged `POST /drafts as office@acmewindows.example`):

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
| Tiles "with logos" | Done, with Simple Icons logos. Twilio and Retell aren't in that set, so they keep their initials. |
| Connectors name, Browse connectors, Claude note | Done |
| Search: publisher, badge, dedupe, ranking, community folded | Done |
| No default for kind of data | Done (client connections; your own MCP tools aren't asked) |
| "Use in all projects" only for your own tools | Done |
| Readable connection names | Done; existing connections are renamed by the migration |
| Linking from the client, the project, the overview | Done |
| No projects: say so, offer Add project and From Claude | Done, on the client page, the success card, agents and routines |
| From Claude cleanup | Wasn't done; done now |
| Archive failed tasks older than 7 days | Done, and clicked (below). It covers failed, timed-out, over-budget and stopped tasks, and only shows when there are some. |
| Subscription first; tasks, architect, routines and router use it | Done. Router, tasks and routines ran on a subscription token here; the architect shares the workers' path, checked in code, not walked. |
| Settings › Google | Done |
| Test failures show the reason on the card | Done |
| Agents and Routines: New, project first, Create an agent | Done |
| No stray scrollbars; 1366×768 and 375 | Done |
| Readable **project** names | Done. A project keeps the name as typed; the short name stays for links and the AI, under Project › Settings. |

# The convenience batch and the Help assistant

Walked the same way, in a fresh scratch install, after journeys a and b.

## @-mentions

`@` in any request box lists clients, projects, connections and agents. A mention is a
firm choice: the router's answer is held to it, whatever the model said.

![Mentions](ux-walkthrough/h01-mention-list.png)

## Files on a request

Dropped on the box, or added with the clip. `brief.txt` landed in the project's
`attachments` folder, and the task was told where.

![Attached](ux-walkthrough/h02-file-attached.png)

**Not clicked with a real drag:** the walk used the clip. The desktop shell had to stop
catching file drops itself for dropping to reach the page; that's in the shell, so it
needs trying in the real app.

## Run again and saved requests

![Run again](ux-walkthrough/h04-task-run-again.png)

Saved requests sit under the box, and the digest card is on Home:

![Home](ux-walkthrough/h05-home-digest-shortcut.png)

**Where I hesitated:** a saved request kept the "Files attached" line and doubled its
`@project`. Fixed. Conversation rows showed the client's short name. Fixed.

## Keys already in a project

With a `.env` in the project, its Connections tab offers what it recognizes, masked.
Twilio's auth token can't be used, and it says why rather than hiding it. The value
never leaves the server: confirming one reads it again from the file.

![Found](ux-walkthrough/h06-keys-found.png)
![Confirm](ux-walkthrough/h07-key-confirm.png)

## Connection health

**Test all** ran every connection (the daily check does the same). A failure shows its
reason on the card:

![Failed test](ux-walkthrough/h21-failed-test-reason.png)

The daily timing, and "key expires in 7 days", are covered by tests, not by waiting a day.

## Approvals

J and K move, A approves, D denies. **Approve all 2 like this** shows for the same action,
with the same details, in one task. I kept that strict on purpose: two different SQL
statements through the same tool are not "like this".

![Approve all](ux-walkthrough/h10-approve-all.png)

After **Approve, and trust this…**, the next activation ran with nothing waiting, and
it's listed with **Stop trusting**:

![Trusted](ux-walkthrough/h11-trusted.png)

## Pins, the digest, the quick box

![Palette](ux-walkthrough/h13-pinned-palette.png)
![Digest setting](ux-walkthrough/h14-digest-settings.png)

| | |
|---|---|
| ![Quick box](ux-walkthrough/h16-quick-box.png) | ![Sent](ux-walkthrough/h17-quick-sent.png) |
| The quick box, at its window size | After Send: it's its own toast, with the link, for 8 seconds |

**The hotkey itself was not pressed.** The page was walked in a browser at the window's
size. The shell code compiles and its tests pass, but running a second shell on this PC
would hand off to your installed app through its single-instance lock, so pressing
Ctrl+Alt+R, the always-on-top window and real file drops are yours to try first.
If another app already uses the key, Ringkeeper says it's taken, and Settings ›
Advanced › Quick request changes it.

The digest's message to Slack, Discord and Telegram is covered by tests with a fake
sender; no real channel was used.

## Old failed tasks

![Archive](ux-walkthrough/h22-archive-offer.png)

## The Help assistant

Asked in the request box, it answers with no project, no task and no approval, cites its
section, and ends with a next step. The reply here is my stand-in's, so the wording isn't
a real model's; the routing, the section, the link and the button are real:

![Help](ux-walkthrough/h18-help-in-the-box.png)

What doesn't exist is answered without calling a model at all, from a section of the
guide written for it:

![Not yet](ux-walkthrough/h19-help-not-yet.png)
![Help › Ask](ux-walkthrough/h20-help-ask.png)

Help › Ask is a conversation: ask a follow-up under an answer, or press **New question** to
start again. The last six turns go with each question, so "why?" knows what it's about.
They aren't a source, and nothing is saved: reloading or signing out ends the conversation.

**Tests:** 20 real questions each find their expected section, and 7 questions about
things that don't exist never reach a model, first or later in a conversation
(`tests/test_help.py`).

**Not tested:** how well a real Haiku follows "answer only from the guide". Its prompt
says so and it only sees those sections, but that needs a real key. Try it with a few
questions you know the answers to.

**Where I hesitated:** while writing "what Ringkeeper doesn't do yet" I had said Activity
can't export, and it can (CSV). That's exactly the mistake this feature must not make, so
that section is worth a read from you: it's the last part of `docs/faq.md`.

## Before beta.15

**Project names.** A project had only its short name, so there was nothing better to show.
It now has a name as typed (`projects.title`), and every page, approval, Slack and
Telegram message, digest and audit line uses it. Existing projects get one from their
short name ("Demo shop"): change it under About this project. The AI still works with
the short name, and is told to say the title to you. A request can mention `@Demo shop`.
Checked by opening every page and tab and looking for each short name on screen; the only
ones left are in folder paths and in what the AI itself wrote.

**Screenshots.** All of them were retaken from a scratch install at `C:\Ringkeeper-demo`,
each checked for a user folder as it was taken. The four `before-*` images are the old
screens and stay, and show no path.

**The quick request key** is now Ctrl+Alt+R: Win+Shift+R is Windows' screen recording.

**Logos** come from Simple Icons (see Settings › About for the notice). **Hide the menu**
is now just the panel icon.
