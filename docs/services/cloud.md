# Clouds: AWS, Azure, Google Cloud and OCI

Each runs the cloud's own server on this computer, with the login you already have in its
command line tool. Ringkeeper holds no cloud key: it only tells the server which profile to
use. This is in the desktop app.

## Add one

Sign in with the cloud's command line tool (`aws sso login`, `az login`, `gcloud auth login`,
or your OCI config). **Connectors** then lists the login under **Signed in on this
computer**. Connect it, and it's your own tool.

The first run downloads the server, so it needs `uv` for AWS and OCI, and Node.js for Azure
and Google Cloud. The form says if one is missing.

## Keeping clients apart

One Azure, Google Cloud or OCI login can often reach many clients, so **each project's link
has to name the client's own** subscription, project or compartment. Until it does, the
project's calls are refused. A command that names a different one is refused too.

For AWS, one connection is one profile, so one account. A link can also pin a region.

On OCI, a command on a project's link has to name that compartment, because Ringkeeper can't
tell which compartment a resource ID belongs to. Listing commands get it added.

Flags that would leave the login, like `--profile`, `--account` or `--endpoint-url`, are
always refused.

## What's held

What a command needs comes from the command, not the tool:

- **Runs:** list, describe, get.
- **Waits for approval:** update, modify, tag, stop, copying files.
- **Needs the name typed, in the app:** deleting, creating or starting anything that costs
  money, anything on IAM, and whatever hands out a secret. So does any command Ringkeeper
  doesn't recognize.

Azure's tools go by their names, the same way. A per-tool setting can make these stricter,
never looser. Nothing here can be undone from Ringkeeper.

## Good to know

- These use your login's own permissions, so give it no more than you want used.
- Oracle marks its server as not meant for production use.
- Versions are pinned, and change only with a Ringkeeper update.
