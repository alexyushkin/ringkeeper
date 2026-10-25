# One real task on the desktop backend

A check of the real-model path before the app shell exists: the desktop
backend from source, your Anthropic API key, one small task. Everything lives
in `.desktop-data` in the repo (git ignores it) and a separate keychain entry,
so it touches neither your Docker install nor a later real install.

It bills your API key: one routing call (Haiku) and one small worker run.
The task page shows what it cost.

All commands are PowerShell, run from the repo root.

## 1. Build

```powershell
git pull
uv sync
cd web; npm ci; node scripts/build-desktop.mjs; cd ..
```

## 2. A throwaway project folder

```powershell
New-Item -ItemType Directory -Force .desktop-data\workspaces\hello-check | Out-Null
git -C .desktop-data\workspaces\hello-check init -b main
git -C .desktop-data\workspaces\hello-check commit --allow-empty -m "start"
```

## 3. Start the backend (terminal 1)

```powershell
$env:RINGKEEPER_ENV_FILE = ""
$env:RINGKEEPER_KEYCHAIN_SERVICE = "Ringkeeper-dev"
$env:RINGKEEPER_WORKSPACES_ROOT = "$PWD\.desktop-data\workspaces"
uv run ringkeeper desktop --web-dir web\.desktop\out --data-dir .desktop-data
```

- `RINGKEEPER_ENV_FILE = ""` keeps your Docker install's `.env` out. Without it
  the backend would pick up that install's settings, including its workspaces
  folder.
- `RINGKEEPER_KEYCHAIN_SERVICE` keeps this run's master key apart from the one
  the real app will use.

Leave it running.

## 4. Open the app (terminal 2)

```powershell
$rt = Get-Content .desktop-data\desktop.json | ConvertFrom-Json
$code = (Invoke-RestMethod -Method Post "$($rt.url)/desktop/codes" -Headers @{ Authorization = "Bearer $($rt.secret)" }).code
Start-Process "$($rt.url)/desktop/start?code=$code"
```

This is what the app shell will do. The link works once, within 60 seconds;
run the three lines again if the browser shows "link expired".

## 5. In the app

1. **Settings → Claude.** Keep "API key (for client work)", paste your key,
   click **Check and save**. It is checked live with Anthropic; you should see
   a green confirmation.
2. **Clients & projects → Add.** Client: `Test`. Project name: `Hello check`.
   Folder: `hello-check`. Leave the rest. Click **Add project**.
3. **Home.** Type and send:

   > In Hello check, add a file hello.txt containing "hello from Ringkeeper" and commit it.

The conversation should show the request going to Hello check, a task
working, then done. Open the task to see what the agent did and the cost.

## 6. Check the result on disk

```powershell
git -C .desktop-data\workspaces\hello-check log --all --oneline
git -C .desktop-data\workspaces\hello-check branch
```

The commit is on a `ringkeeper/<task id>` branch; `main` still has only
"start".

## 7. Stop and clean up

Ctrl+C in terminal 1 stops everything. Then:

```powershell
Remove-Item -Recurse -Force .desktop-data
uv run python -c "import keyring; keyring.delete_password('Ringkeeper-dev', 'master-key')"
```

Removing `.desktop-data` also removes the saved API key (it is in the
encrypted vault there).

## If it fails

Send the output of terminal 1, the task page text, and the `.log` files under
`.desktop-data\workers\`. None of them contain your key.
