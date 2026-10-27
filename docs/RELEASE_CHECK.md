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

| | |
| --- | --- |
| Installer (download) | **246.9 MB** (`Ringkeeper_0.1.0-beta.1_x64-setup.exe`, SHA-256 `D24641230A72D997ADB8410B9ABAAD2873FE2D8DD01155CD8BF5947F20F11B5C`) |
| Installed | **846 MB**, 21,222 files |

What takes the space (installed):

| Part | MB | Notes |
| --- | ---: | --- |
| Claude Code CLI (`claude.exe`, inside the Agent SDK) | 285 | what agents run on; required |
| Local voice (faster-whisper's engine: av, ctranslate2, onnxruntime, numpy, tokenizers, huggingface) | ~238 | code only; the speech model downloads on first use |
| MinGit (git for workers) | 96 | ~25 MB of it is Git Credential Manager, which workers don't use |
| Ringkeeper's other Python packages | ~110 | anthropic, AWS SDK (server mode only, ~29 MB), SQLAlchemy, cryptography, ... |
| Precompiled Python (`.pyc`) | 74 | makes the first start faster |
| Python itself (runtime and standard library) | ~40 | |
| App, web app, notices | ~16 | |

What could be trimmed, biggest first:

1. **Voice as an optional download** (its code, like its model): −238 MB
   installed, roughly −70 MB download. Voice would then ask to download ~300
   MB the first time instead of ~240 MB.
2. **Drop Git Credential Manager from MinGit**: −25 MB; workers authenticate
   with tokens, not GCM.
3. **Move the AWS SDK to a server-only extra**: −29 MB and ~2,300 fewer files
   (it's only for the AWS Secrets Manager vault in server mode).
4. **Ship compiled Python only** (no `.py`): −70 MB and ~8,000 fewer files,
   which shortens install and uninstall too; error reports lose source lines.
5. **Don't precompile**: −74 MB and ~8,000 fewer files; the first start gets
   slower (Python compiles on first use).

Not trimmable: the Claude Code CLI is one 285 MB program, and it's what runs
the agents.

### Scripted checks (developer machine, Windows 11 Pro 26300)

| Check | Result |
| --- | --- |
| Defender | installer scan: no threats; real-time protection on through about ten installs, updates and uninstalls: no detections |
| Silent install (`/S`) | 41–61 s (Defender scans each new file); per user, no administrator prompt |
| First start (database, master key) | backend ready in 6–21 s |
| Settings › About | version 0.1.0-beta.1, 463 third-party components with their notices |
| Start at sign-in | registered as `<install folder>ingkeeper.exe --hidden` |
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
