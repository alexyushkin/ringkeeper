# Recipes

A recipe is an agent and its routines as one file: the agent's instructions and settings, and
each routine's prompt and timing. Use one to start from, or share your own.

A recipe never has a key, a connection's name, or anything that points at one client. Which
connection to use, and what an event watches (a repository, a website, an Airtable base), are
chosen when you add it. It can't run code: a recipe is only settings and instructions, and a
file with anything else in it is refused.

## Add one

On a project's Routines, **New routine** shows the routine templates and, below them,
**recipes**:

- **The starters:** 13 common jobs, from fixing a failed n8n run to a weekly client report, or
  acting on an email you forward to yourself.
- **Import a recipe from a file or a link:** a `.json` file, or an https link to one. A GitHub
  page for a file works too.

Before anything is added you see the whole recipe: the agent's instructions, each routine with
its prompt and when it runs, and what it needs:

- **A connection of each kind the agent uses,** from the ones linked to this project. If the
  project has none of that kind, link one first, or add the recipe without it and give the agent
  one later on its page. A routine that watches a service (an n8n failure, a GitHub check) needs
  that connection before the recipe can be added.
- **For a routine that runs when something happens,** what to watch: a repository, a website's
  address, an Airtable base and table, or a Vercel project.

**Add to this project** adds the agent and its routines. The routines start paused: run each
once from its page to check it, then turn it on. If the project already has an agent with that
name, the new one gets a number (`fixer-2`); nothing already there changes.

## Share your own

On an agent (a project's Agents), **Export as recipe** saves the agent and its routines as a
file, `<agent>.ringkeeper-recipe.json`. It keeps:

- the agent's instructions, model, effort, autonomy and memory setting, and the kinds of
  connection it uses (not which ones);
- each routine's name, prompt, timing, limits and notifications, and for an event only what
  kind it is (an n8n failure, a GitHub check, a website).

It leaves out connection names, what an event watches, time zones, and anything that looks like
a secret. Read it before you share it: what you wrote in the instructions and prompts is in it.

A recipe's Gmail event may say which mail starts a run, in Gmail's search words: the Forward an
email starter watches only `from:me to:me subject:(Fwd OR Fw)`. An exported recipe never has
one, since a search can name a client.

## The file

A recipe is JSON with `"ringkeeper_recipe": 1`, its version. A later Ringkeeper reads older
versions; an older one says to update before adding a newer one. The starters are in the
[recipes](https://github.com/alexyushkin/ringkeeper/tree/main/recipes) folder.
