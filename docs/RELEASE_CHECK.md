# Release check: Windows installer

What each release must pass before it's handed out, and the results for
**0.1.0-beta.1** (unsigned).

## The checklist

On a clean Windows 11 (Windows Sandbox or a fresh VM), as a user would, with
no terminal:

1. Download the installer in a browser, run it. Note every prompt and warning
   (browser, SmartScreen, installer pages) and the time from double-click to
   Ringkeeper's first-run screen. Target: under 5 minutes, including first run.
2. First run: API key, a project, one small task. Settings › About shows the
   version and the third-party notices.
3. Close the window (it stays in the tray), sign out and in: it starts hidden
   in the tray (start at sign-in).
4. Update: from the previous release, "Update ready, restart when you're done"
   appears; Restart now; the new version opens with the same data.
5. Uninstall (Settings › Apps): without "Also delete my data", the data stays
   and a reinstall finds it; with it, `%LOCALAPPDATA%\Ringkeeper` and the
   keychain entry are gone.
6. Microsoft Defender: a scan of the installer and the installed folder finds
   nothing.
7. If possible, the same on Windows 10 22H2 (there the installer installs the
   WebView2 Runtime first).

### Windows Sandbox

Turn it on once (Windows 11 Pro or Enterprise, as administrator): Settings ›
System › Optional features › More Windows features › **Windows Sandbox**, then
restart. To give the sandbox the installer, save this as
`ringkeeper-check.wsb` next to the installer's folder and open it (each run
starts clean):

```xml
<Configuration>
  <MappedFolders>
    <MappedFolder>
      <HostFolder>C:\path\to\the\installer\folder</HostFolder>
      <SandboxFolder>C:\Users\WDAGUtilityAccount\Desktop\Installer</SandboxFolder>
      <ReadOnly>true</ReadOnly>
    </MappedFolder>
  </MappedFolders>
  <MemoryInMB>8192</MemoryInMB>
</Configuration>
```

(For the SmartScreen part, download the installer inside the sandbox with
Edge instead: a mapped file doesn't carry the "downloaded from the internet"
mark that triggers it.)

### Scripted checks on a developer machine

The install, update, failure and uninstall checks below were also run by
script on the developer's machine, with the app's data folder, keychain entry
and project folder redirected to scratch locations
(`RINGKEEPER_SHELL_DATA_DIR`, `RINGKEEPER_KEYCHAIN_SERVICE`,
`RINGKEEPER_WORKSPACES_ROOT`), so a real install there is never touched. The
uninstaller's `/DELETEDATA` switch ticks "Also delete my data" for silent runs.

## Results: 0.1.0-beta.1

### Sizes

After moving voice to a download and dropping Git Credential Manager and the
AWS SDK (installed sizes in MiB, as Explorer shows them):

| | Before | Now |
| --- | ---: | ---: |
| Installer (download) | 246.9 MB | **165.0 MB** |
| Installed | 846 MB, 21,222 files | **555 MB, 16,687 files** |
| Silent install | 41–61 s | 53 s |
| Uninstall | 22 s | 69 s (on a busy machine: the test suites were running) |

What takes the space now (installed):

| Part | MB | Notes |
| --- | ---: | --- |
| Claude Code CLI (`claude.exe`, inside the Agent SDK) | 283 | what agents run on; required |
| Ringkeeper's other Python packages, Python itself, `.pyc` | ~192 | |
| MinGit (git for workers) | 64 | without Git Credential Manager (was 96) |
| App, web app, notices | ~16 | |

Voice is now one download the first time it's used, with its size shown
first and then its progress: the engine's 13 wheels from PyPI (84 MB, each
checked against the SHA-256 the installer lists) and the speech model. The
default, Fast (faster-whisper's `tiny`), is 78 MB, so about 162 MB in all;
Accuracy (its `small`, chosen in Settings › Voice) is 486 MB, about 570 MB in
all. Only the chosen model is kept. The build runs that same engine download and
imports it, so a broken wheel list fails the build, not a user's first try.

Further trims, not done:

1. **Ship compiled Python only** (no `.py`): about −70 MB and ~8,000 fewer
   files, which shortens install and uninstall; error reports lose source lines.
2. **Don't precompile**: about −74 MB and ~8,000 fewer files; the first start
   gets slower.

Not trimmable: the Claude Code CLI is one program, and it's what runs the
agents.

### Scripted checks (developer machine, Windows 11 Pro 26300)

| Check | Result |
| --- | --- |
| Defender | installer scan: no threats; real-time protection on through about ten installs, updates and uninstalls: no detections |
| Silent install (`/S`) | 41–61 s (Defender scans each new file); per user, no administrator prompt |
| First start (database, master key) | backend ready in 6–21 s |
| Settings › About | version 0.1.0-beta.1, 463 third-party components with their notices (456 after the trims, including the 13 voice packages, listed as downloaded for voice) |
| Start at sign-in | registered as `<install folder>\ringkeeper.exe --hidden` |
| Install folder longer than 90 characters | refused (Windows' 260-character path limit; the bundle's deepest file is 158 characters below the folder) |
| Update 0.1.0-beta.1 → 0.1.0-beta.2 via the updater | found 15–30 s after start, downloaded and its signature checked; "Restart now" stops the backend and runs the installer; the new version reports 0.1.0-beta.2, reopens by itself with the same arguments, and has the same projects and database. About 2.5 minutes from Restart now to reopened (test installers were uncompressed, on a busy machine); the old copy is then removed in the background |
| Update that fails halfway (a file the installer can't write) | installer stops (exit code 2), the previous version is put back unchanged (same files, same program), and it starts with its data |
| Uninstall, keeping data | 22 s; install folder empty; Apps entry gone; data and keychain entry kept; a reinstall finds the data |
| Uninstall with "Also delete my data" | data folder and keychain entry deleted; other keychain entries untouched; start-at-sign-in entry gone; install folder gone |
| Quit (`ringkeeper --quit`) | everything stops in under 2 s when idle |

### Clean machine (no terminal)

| Check | Result |
| --- | --- |
| Windows 11, Windows Sandbox: download, install, first run | **not run yet**: Windows Sandbox isn't turned on on the build machine |
| Windows 10 22H2 | **not run**: no Windows 10 machine or VM available |
| SmartScreen | expected for an unsigned installer: "Windows protected your PC" → More info → Run anyway |
