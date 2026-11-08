# Ringkeeper recipes

Each file here is a [Ringkeeper](https://github.com/alexyushkin/ringkeeper) recipe: an agent
and its routines, with no keys and no client's names. Ringkeeper ships these as its starters:
the same files are in its own `recipes/` folder, and a test there keeps them the same.

To use one, in Ringkeeper open a project's Routines, click **New routine**, and pick it under
recipes, or choose **Import a recipe from a file or a link** and paste the file's GitHub link, like
`https://github.com/alexyushkin/ringkeeper-recipes/blob/main/daily-digest.json`.
You see everything in it before it's added, and its routines start paused.

| Recipe | What it does | Needs |
|---|---|---|
| `fix-failed-automation` | When an n8n run fails, finds out why and proposes a fix | n8n |
| `website-down` | When the site stops answering, takes a first look | - |
| `vercel-deploy-failed` | Explains a failed Vercel deploy and the likely fix | Vercel |
| `github-check-failed` | Finds the failing step of a GitHub check | GitHub |
| `github-issue-triage` | Sums up a new issue and drafts a reply | GitHub |
| `daily-digest` | A weekday-morning digest of what changed | - |
| `weekly-client-report` | Drafts the week's report for the client on Fridays | - |
| `lead-follow-up` | Drafts a reply to a new lead sent to its webhook | - |
| `data-sync-check` | Checks every morning that connected systems agree | - |
| `inbox-triage` | Sorts new Gmail and drafts replies to urgent mail | Google |
| `forward-an-email` | Does what your note says on an email you forward to yourself | Google |
| `airtable-new-record` | Checks a new Airtable record for missing or odd fields | Airtable |
| `monthly-housekeeping` | Lists stale records, idle automations and expiring keys | - |

Changes to production, sent messages and charges wait for your approval in Ringkeeper, whatever
a recipe says. The format is described in [Recipes](https://github.com/alexyushkin/ringkeeper/blob/main/docs/recipes.md).
