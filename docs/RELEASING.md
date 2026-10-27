# Releasing the desktop app

## Where releases go

Installers and update files go to a separate public repository,
**`alexyushkin/ringkeeper-releases`**, which holds no code, only:

- `README.md`: what Ringkeeper is, a download link to the latest release, the
  SmartScreen note (the same text as the main README's Install section), and
  where to report problems.
- `LICENSE`: Ringkeeper's MIT license, with a line saying the installers also
  contain third-party software, listed in the app under Settings › About.
- **Releases**, one per version (`v0.1.0-beta.1`, ...), made by the release
  workflow, each with:
  - `Ringkeeper_<version>_x64-setup.exe`: the Windows installer
  - `Ringkeeper_<version>_x64-setup.exe.sig`: its updater signature
  - `latest.json`: what installed apps read to find updates
  - on macOS builds: `Ringkeeper_<version>_aarch64.dmg`,
    `Ringkeeper.app.tar.gz` and its `.sig` (unsigned, not advertised)

Installed apps check
`https://github.com/alexyushkin/ringkeeper-releases/releases/latest/download/latest.json`.
"Latest" only ever means a normal (not pre-release) GitHub release, so the
workflow publishes betas as normal releases; "beta" is in the version.

## One-time setup

1. Create the public repository `alexyushkin/ringkeeper-releases` with the
   README and LICENSE above.
2. Create a fine-grained personal access token: only that repository,
   **Contents: Read and write**. In the code repository, add it as the
   Actions secret `RELEASES_TOKEN`.
3. Add the updater key as secrets `TAURI_SIGNING_PRIVATE_KEY` and
   `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` (see [SIGNING.md](SIGNING.md#the-updater-key)).
4. Optional: code signing ([SIGNING.md](SIGNING.md)).

## Each release

1. Add a section to `CHANGELOG.md`: `## 0.1.1 (2026-11-02)` and what changed,
   in users' words.
2. Push it, then tag and push the tag:

   ```bash
   git tag v0.1.1 && git push origin v0.1.1
   ```

3. The **Release** workflow builds, signs if it can, and publishes. Check the
   new release in `ringkeeper-releases`, then install it on a clean machine
   (docs/RELEASE_CHECK.md has the checklist).

A test build without publishing: Actions → Release → Run workflow. Its files
are in the run's artifacts.
