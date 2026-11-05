# Where Claude runs

By default, Claude runs on Anthropic's own service, with an API key or a Claude subscription.
You can run it on **Amazon Bedrock**, **Google Vertex AI** or **Microsoft Foundry** instead,
for example when a client's data has to stay in their own cloud, or their spend has to go
through it.

**Settings › Claude › Where Claude runs** sets the default. Tasks, the architect and the
router all run there. A project can use another: open its **Settings** tab. That choice
appears once a second place is set up.

## Amazon Bedrock

In the Bedrock console, submit the Anthropic use case form once. Then give the region and
either:

- **a Bedrock API key**, kept in the vault; or
- **an AWS profile on this computer.** Its short-lived credentials are read each time a
  session starts, so Ringkeeper stores no key. If they've run out, the task says to sign in
  again with `aws sso login`.

## Google Vertex AI

Enable Vertex AI and the Claude models in Model Garden. Give the project ID, the region
(`global` often works), and a service account key file with the Vertex AI User role.

The key is kept in the vault. Claude Code needs it as a file, so a running task gets a copy
in its own private folder, which the AI working on that task can reach.

## Microsoft Foundry

Create a resource and one deployment per Claude model in the Foundry portal. Give the
resource's name, and either its API key or **Use my Azure CLI login**. Foundry has no default
models, so fill in your deployment names.

## Models

Each cloud has its own model IDs. **Models on this platform** pins one per family (Opus,
Sonnet, Haiku), and Ringkeeper uses it wherever that family is chosen. Left empty, Claude
Code uses its own defaults for that platform, which can bill at the Opus rate.

## Check it

**Check it** sends one tiny request the way a task would, so it proves the login, the region
and the model together. Your cloud bills it, by a few tokens.

## Good to know

- A task's spending cap still applies on a cloud.
- The Help assistant and the router run on the default place. A project's choice covers that
  project's tasks.
- Web search isn't available to Claude on Bedrock.
