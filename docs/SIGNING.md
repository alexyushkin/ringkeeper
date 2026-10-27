# Signing the Windows app

Signing is optional and pluggable. Without signing secrets, the release
workflow (`.github/workflows/release.yml`) builds an **unsigned** installer,
and Windows SmartScreen warns when people run it (the README tells them how to
get past it). With secrets, it signs everything it ships that isn't signed
already, under **General Informatics LLC**:

- the app (`ringkeeper.exe`), the uninstaller, the installer's plugins and the
  installer itself (Tauri runs `shell/scripts/sign.ps1` for each), and
- the bundled programs and libraries that come unsigned: Python's, and some
  of MinGit's (about 380 files). The Claude Code CLI is already signed by
  Anthropic and is left as it is.

Two kinds of signing are supported: **Azure Artifact Signing** (preferred) and,
as a fallback, **any other certificate signtool can use**, such as an OV
certificate.

Updates are signed separately, with the updater key (see "The updater key"
below). That signing is always on, and it is what installed apps check before
installing an update.

## Azure Artifact Signing

Azure Artifact Signing (formerly Trusted Signing) keeps the certificate in
Azure and signs on request, so there's no certificate file to store or renew.
It costs a monthly fee (see the Basic tier on Azure's pricing page; it includes
more signatures a month than releases use).

### Set it up in Azure

1. **A subscription.** In the [Azure portal](https://portal.azure.com), use (or
   create) a pay-as-you-go subscription for General Informatics LLC.
2. **Register the provider.** Subscriptions → your subscription → Resource
   providers → `Microsoft.CodeSigning` → Register.
3. **Create an Artifact Signing account.** Search "Artifact Signing" → Create.
   Pick a resource group, a name (for example `geninfos-signing`), a region and
   the Basic tier. Note the account's **endpoint**, shown on its overview page
   (for East US it's `https://eus.codesigning.azure.net`).
4. **Validate the organization.** In the account: Identity validations → New →
   **Organization**, **Public Trust**. Enter General Informatics LLC's legal
   details exactly as registered, and follow the email and document requests.
5. **Create a certificate profile.** Certificate profiles → Create → **Public
   Trust**, using that identity validation. Note the profile's name (for
   example `ringkeeper`).
6. **An identity for GitHub.** Microsoft Entra ID → App registrations → New
   registration (name it `ringkeeper-release-signing`). Note its **Application
   (client) ID** and **Directory (tenant) ID**. Certificates & secrets → New
   client secret; copy the secret's **value**.
7. **Let it sign.** On the Artifact Signing account: Access control (IAM) →
   Add role assignment → **Artifact Signing Certificate Profile Signer** →
   the app registration from step 6.

### Tell the release workflow

In the `ringkeeper` repository: Settings → Secrets and variables → Actions.

| Kind | Name | Value |
| --- | --- | --- |
| Secret | `AZURE_TENANT_ID` | the directory (tenant) ID |
| Secret | `AZURE_CLIENT_ID` | the application (client) ID |
| Secret | `AZURE_CLIENT_SECRET` | the client secret's value |
| Variable | `AZURE_SIGNING_ENDPOINT` | the account's endpoint, e.g. `https://eus.codesigning.azure.net` |
| Variable | `AZURE_SIGNING_ACCOUNT` | the account's name |
| Variable | `AZURE_SIGNING_PROFILE` | the certificate profile's name |

The next tag builds a signed installer. The workflow's **Signed?** step prints
the installer's signature; it should say `Valid` and name General Informatics
LLC. The client secret expires (at most two years): renew it in the app
registration before then and update `AZURE_CLIENT_SECRET`.

## Fallback: an OV certificate

If Artifact Signing isn't available, buy an **OV (organization validated)
code-signing certificate** for General Informatics LLC from a certificate
authority (DigiCert, Sectigo, SSL.com, Certum and others). Since 2023 their
private keys must stay in hardware: the CA delivers either a USB token or,
more practical for CI, a **cloud key service** (DigiCert KeyLocker, SSL.com
eSigner, Certum SimplySign) that signtool reaches through the CA's key
storage provider.

1. Follow the CA's instructions for using its cloud key service with signtool
   on a GitHub Actions Windows runner. That usually means installing the CA's
   client tool and setting a few of its own environment variables or secrets.
   Add those steps to the `windows` job, before **Sign the bundled programs**.
2. Set these in the repository:

   | Kind | Name | Value |
   | --- | --- | --- |
   | Secret | `RK_SIGN_ARGS` | signtool's certificate arguments, on one line. For DigiCert KeyLocker, for example: `/csp "DigiCert Signing Manager KSP" /kc <key alias> /f <certificate file>` |
   | Variable | `RK_SIGN_TIMESTAMP` | the CA's timestamp server (default `http://timestamp.digicert.com`) |

`sign.ps1` then signs with `signtool sign /fd SHA256 /td SHA256 /tr
<timestamp> <your arguments> <files>`. Azure settings win when both are set.

A new OV certificate starts with no reputation, so SmartScreen can still warn
for a while after signing begins; the warning stops as the signed installer is
downloaded and run without problems.

## The updater key

Installed apps install an update only if it's signed with Ringkeeper's updater
key; the matching public key is in `shell/tauri.conf.json`
(`plugins.updater.pubkey`). The private key was generated on the release
manager's computer and is **never committed**.

- **Where it lives:** the GitHub secrets `TAURI_SIGNING_PRIVATE_KEY` (the
  contents of the key file) and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD`. The
  original files are in `%USERPROFILE%\.ringkeeper-release\` (`updater.key`,
  `updater.key.password`, `updater.key.pub`) on the computer that made them.
- **Back it up:** store `updater.key` and its password in a password manager
  (or two separate offline places). Then delete the local copies if you like.
- **If it's lost:** installed apps can't be updated with a new key. Users would
  have to download and run a new installer by hand, once (their data stays).
  Generate a new pair (`npx @tauri-apps/cli@2.12.2 signer generate`), put the
  new public key in `tauri.conf.json` and the private key in the secrets.
- **To change it** (say it may have leaked): generate a new pair, and release
  one version signed with the **old** key whose `tauri.conf.json` carries the
  **new** public key. Installed apps accept that update and trust only the new
  key from then on; sign everything after it with the new key. (A leaked key
  alone isn't enough to push an update: the app downloads only from the
  releases repo.)
