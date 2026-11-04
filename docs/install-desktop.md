# Install the desktop app

## Windows 10 and 11

Download `Ringkeeper_<version>_x64-setup.exe` from the
[latest release](https://github.com/alexyushkin/ringkeeper-releases/releases/latest)
and run it. It installs for your user only: no administrator rights, nothing
else to install, no terminal. Ringkeeper opens when it's done and walks you
through first run; you need an Anthropic API key
([platform.claude.com](https://platform.claude.com) → API keys).

**Windows may warn you first.** Until the installer is signed and well known,
Microsoft Defender SmartScreen shows **"Windows protected your PC"**. Click
**More info**, check that the app is `Ringkeeper_..._x64-setup.exe`, then
click **Run anyway**. (If your browser flags the download, choose **Keep**.)
If instead **Smart App Control** blocks it (some new Windows 11 PCs have it
on), there's no "Run anyway": use a signed release once there is one.
Turning Smart App Control off is possible in Windows Security, but it can't be
turned back on without reinstalling Windows.

- **Where things go:** the app in `%LOCALAPPDATA%\Programs\Ringkeeper`, your
  data in `%LOCALAPPDATA%\Ringkeeper` (the master key that protects your saved
  keys is in Windows Credential Manager), new projects in
  `Documents\Ringkeeper`.
- **Updates:** Ringkeeper checks when it starts and every 6 hours (turn off
  **Check automatically** in Settings › About to check only when you ask),
  downloads updates in the background and shows **Update ready**. To check
  now, use **Check for updates** in Settings › About or the tray menu: it
  shows when it last checked, "You're up to date", the new version with its
  release notes and **Install and restart**, or what went wrong (offline, a
  signature that didn't verify). It never restarts by itself: while tasks run,
  the button is **Restart when tasks finish**. It never touches your data, and
  if an update fails, the old version keeps working.
- **Uninstall:** Settings → Apps → Ringkeeper → Uninstall. Your data stays
  unless you tick **Also delete my data**; your project folders always stay.

## Mac, experimental

**Experimental: unsigned, not yet tested on a real Mac — reports welcome.**
Each release also has two Mac downloads, for macOS 12 or newer. To see which
is yours, open the Apple menu › **About This Mac**:

- **Chip: Apple M1** or newer: `Ringkeeper_<version>_aarch64.dmg`
- **Processor: Intel**: `Ringkeeper_<version>_x64.dmg`. Voice isn't available on
  Intel Macs, because the speech engine no longer supports them. Type your requests instead.

1. Download the `.dmg` from the
   [latest release](https://github.com/alexyushkin/ringkeeper-releases/releases/latest),
   open it, and drag **Ringkeeper** into **Applications**.
2. Open Ringkeeper from Applications. macOS says it can't check the app for
   malware, because it isn't signed yet. Click **Done**.
3. Open **System Settings › Privacy & Security**, scroll down to **Security**,
   and next to "Ringkeeper was blocked…" click **Open Anyway**. Confirm with
   your password or Touch ID, then click **Open**. macOS remembers this, so you
   only do it once.

If macOS instead says the app "is damaged and can't be opened", that's the same
missing signature. In Terminal, run
`xattr -dr com.apple.quarantine /Applications/Ringkeeper.app`, then open it
again.

Your data goes in `~/Library/Application Support/Ringkeeper`, the master key in
your Keychain, and new projects in `Documents/Ringkeeper`. Updates install from
inside the app, as on Windows.

Something not working on your Mac? Please
[open an issue](https://github.com/alexyushkin/ringkeeper/issues/new/choose):
that's how the Mac build gets tested.

## First run

1. **Claude:** paste your Anthropic API key (or, for personal use, sign in with
   your Claude subscription). Ringkeeper checks it with Anthropic before saving.
2. **Projects:** add your first project, pick up the folders Claude Code already
   knows, or **Try it with demo data** to look around first.
3. **Connections:** connect a client's service, or skip for now.

Then Home has a short **Get started** checklist and a three-step tour. See
[Concepts](concepts.md) for how it fits together.

## Moving to a new computer

Make a backup in Settings › Backup, install Ringkeeper on the new computer, and
restore it there: see [Backup](backup.md).

Running Ringkeeper on a server instead: [Install with Docker](install-server.md).
