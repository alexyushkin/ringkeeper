# Desktop app plan

Status: **approved 2026-10-25**, with the decisions below. They override anything later in this document.

## Approved decisions

- **Platforms.** Windows is the only supported desktop target at launch. macOS code paths keep working, and CI builds an unsigned macOS `.dmg` on tags only. No Apple account, signing or notarization for now; the Mac docs are a short "unsigned beta" note with the Open Anyway steps.
- **Windows signing.** Azure Artifact Signing under General Informatics LLC. It doesn't block M1–M5.
- **Deviations accepted.** The keychain holds only the master key, and secrets stay in the encrypted vault file (Q3). The backend ships as embedded Python.
- **Connectors (Q1).** API tokens in v1: Neon, Supabase, Airtable PAT, Twilio, Vercel, n8n and similar. Each connector has a "where do I get this" link and a live test button. OAuth only where no token option exists.
- **Gmail (Q2).** No Google OAuth in v1. Gmail uses IMAP/SMTP with an app password. When the account or a Workspace admin blocks app passwords, the UI says so plainly.
- **Updates (Q4).** A separate public releases repository holds installers and update manifests.
- **macOS sandbox (Q7).** Not in v1; a post-launch item.
- **Q5 (voice model size) and Q8 (keep running in the tray).** My recommendation, but I ask before shipping anything that changes what the user sees or pays. Q6 is moot (Windows only).
- **CI.** Packaging jobs (Windows installer, unsigned macOS build) run on tags only. Regular CI runs the tests against Postgres and SQLite, and includes Claude Code's PowerShell fallback on Windows (no bash).
- **Definition of done.** A clean Windows 11 machine: download the signed installer, open it, paste an API key, create a project, connect one service, run one task. Under 5 minutes, zero terminal use.

Goal: an agency owner downloads Ringkeeper, double-clicks it, pastes an Anthropic
API key, creates a project, connects one service and runs one task. That should
take under 5 minutes and need no terminal, Docker, Python, Node, `.env` file or setup code.

The Docker install stays as **advanced mode**, for always-on routines and phone
access over Tailscale. It must keep working unchanged. Both modes run the same
Python core: routing, connectors, approvals, projects and tasks.

---

## Progress

- **M1 done** (2026-10-25): storage seams in `src/ringkeeper/storage/`, SQLite schema
  and migrations, keychain master key, `RINGKEEPER_MODE`. CI: Postgres 3.12/3.13,
  SQLite, Windows.
- **M2 done** (2026-10-25): `ringkeeper desktop`; workers as local processes
  (`workers/process.py`); the front door's lock (`desktop/server.py`); the web app
  exported as static files (`web/scripts/build-desktop.mjs`). The real Claude Code CLI
  runs as a desktop worker against a fake API in tests, and CI checks the PowerShell
  fallback on Windows without Git Bash.

- **M3 done** (2026-10-25): the app shell in `shell/` (Tauri 2). It starts the
  backend, restarts it after a crash (1, 2, 4, 8 s apart; after five crashes in two
  minutes it stops and offers Try again), finds a running backend again after the
  shell itself crashed, and Quit stops everything cleanly. Closing the window keeps
  it in the tray, with a one-time notice. Tray menu: Open Ringkeeper, Pause all
  routines (the existing pause: nothing new starts, requests wait, running tasks
  finish), Quit. A red dot on the tray and taskbar icons and a Windows notification
  when something is waiting for the user. "Start Ringkeeper when I sign in" is a
  setting (`start_at_login`, on by default) the shell applies. `ringkeeper --quit`
  quits a running app (for the installer). CI builds and tests it on Windows
  (`.github/workflows/desktop-shell.yml`: when the shell or the desktop backend
  changes, and on every tag).
- **M4 done** (2026-10-26): first run in the app. A fresh data folder opens on
  three steps: the Anthropic API key (checked with the free models call; no
  subscription option on the desktop), the first project, and "Start Ringkeeper
  when I sign in" (checked by default). A project may use any folder on the
  computer (typed, or picked with Browse…, the shell's native folder dialog); left
  empty, it gets a new one in Documents\Ringkeeper. A new or empty
  folder is started as a git repository with an empty first commit on `main`; a
  folder with files is left as it is. The sign-in, password, passkey,
  recovery-key and sign-out screens are gone from the desktop app; Telegram
  pairing moved into Settings there. The microphone is hidden until the Voice
  component.
- **M5 done** (2026-10-26): connections per project. Built-in adapters for every
  v1 service, no proxied vendor MCP servers: only Neon, Supabase and n8n run a
  remote MCP server that takes a plain token (Vercel's is OAuth-only for
  approved clients, Twilio's only searches docs, Airtable's token use is
  undocumented), and none label tools read or write. Databases (Supabase, Neon
  or any Postgres) go through the SQL adapter by connection string; n8n keeps
  its adapter; new adapters for Airtable, Vercel (reads; redeploy, promote and
  roll back; env variable names only), Twilio and Gmail (IMAP/SMTP with an app
  password). Each tool has a hold rule: sending a text or an email always waits
  for approval, even on test data; a Gmail draft never does. A connection is
  added from the project's page (or first run's optional "Connect a service"
  step), with a "where do I get this" link and Test, and linked to that
  project. A worker only gets the endpoints of its project's services. The
  no-cross-talk test (two clients, two databases, the real gateway and worker
  tokens) passes with Ringkeeper's own database on SQLite and on Postgres. First
  run also offers the folders the user opened Claude Code in as projects.
  Found on the way: on SQLite a write could fail with "a write transaction is
  already open" when FastAPI finished a request on another thread (fixed).

Left for later milestones:

- **After v1:** a generic "MCP server" connection (any remote URL plus a token,
  every tool held unless marked read-only) and the Claude Desktop import that
  depends on it.
- **M6:** bundle the embedded Python at `resources/python` and the web files at
  `resources/web` (the shell looks there), and MinGit at `resources/mingit`
  (`shell/scripts/fetch-mingit.ps1`, pinned and checked). The app identifier is
  `com.geninfos.ringkeeper` (final: it names the app to Windows, so it must not
  change after the first release). In a dev build, notifications show as from
  PowerShell; an installed app shows its own name.
- **Not verified yet:** a task with a real model (`docs/DESKTOP_REAL_TASK_CHECK.md`),
  and anything on macOS.
- **To watch:** SQLite allows one writer at a time, so a request that holds a write
  transaction while it waits on the network blocks the others (fine for one user;
  revisit if it shows).

## 1. Decisions in one page

| Area | Your default | My recommendation | Why |
|---|---|---|---|
| Shell | Tauri, loading the existing web UI | **Tauri 2**, agreed | Small, native tray, sidecar support, updater built in |
| How the web UI is served | (not specified) | **Static export of the Next.js app, served by the Python sidecar**. No Node in the installer | Every page is already a client component. Only the BFF, `proxy.ts` and the CSP nonce need a server, and the sidecar can do those jobs. See §4.6 |
| Backend packaging | PyInstaller or embedded uv Python | **Embedded Python** (python-build-standalone via uv) with a relocatable venv shipped as app resources | Workers run as `python -m ringkeeper_worker` on the same interpreter. No PyInstaller hooks for pydantic, fastmcp, psycopg or the SDK's bundled `claude` binary. Far fewer antivirus false positives than PyInstaller's onefile bootloader, which matters a lot for unsigned Windows builds |
| Storage | SQLite (desktop), Postgres (server) | **SQLite**, agreed. Embedded Postgres not needed | About 20 Postgres-specific spots, all portable (§2.2). Embedded Postgres would add 30–40 MB per platform, a second server process to supervise, and painful major-version upgrades on users' machines |
| Secrets | OS keychain in desktop mode | **Override, partly:** the keychain holds **only the master key**. Secrets stay in the existing encrypted vault file in the app data folder | Windows Credential Manager caps a secret at 2,560 bytes (Google service-account JSON is close to that). macOS asks permission per keychain item whenever the app's signature changes, so unsigned test builds would prompt for every secret after every update. One keychain item avoids both. It's still one `Vault` interface, and a `keychain://` backend can be added if you want it |
| Workers | Subprocesses | **Subprocesses**, agreed, with the limits in §3 | |
| Auth | No login on 127.0.0.1 | **No login, no setup code**, but a per-launch secret only the app window holds (§4.5) | Any website open in the user's browser can send requests to `127.0.0.1`. "No login" must not mean "no lock" |
| STT | Optional download | Agreed: a separate "Voice" component downloaded on first use | faster-whisper plus the model is 250–600 MB |
| Platforms | Windows, macOS first | Agreed. Windows x64, macOS arm64 and x64. Linux later | The SDK has no bundled CLI for Windows ARM64 |

Things only you can decide are collected in **§8**.

---

## 2. What ties Ringkeeper to Docker and server mode today

### 2.1 Process layout

`docker-compose.yml` runs these services:

| Service | What it does | Docker-specific parts |
|---|---|---|
| `postgres` | All state | Separate server; LISTEN/NOTIFY used for live updates |
| `api` | FastAPI, plus the session manager, router, Telegram bridge and supervisor in-process | Runs as root because it holds the **Docker socket** to start workers. Writes files for other containers' uids (`RINGKEEPER_DATA_UID=10001`, `RINGKEEPER_WEB_UID=10003`). Applies migrations on start (`ringkeeper db upgrade`) |
| `gateway` | MCP gateway that workers call. Holds the connection secrets (read-only vault) and enforces approvals | Separate container on the `workers` network, so workers can reach it but not Postgres or the api |
| `web` | Next.js server: pages, BFF (`/bff/*`), `proxy.ts` (host check, CSP nonce) | Reads the web token from a shared volume (`/run/ringkeeper-web/api-token`). Read-only filesystem |
| `worker` (image) | Per-session container: Python, `claude-agent-sdk` 0.2.174 (bundles the `claude` CLI), git, pytest, uv | Started by `DockerRuntime` (`workers/runtime.py`) |
| `stt` (profile) | faster-whisper over HTTP on `internal` | Docker memory, CPU and pid limits; read-only filesystem |
| `demo-targets` (profile) | Three fictional client databases (plain Postgres) | Demo only |

### 2.2 Postgres-specific features (schema 0001–0008 and queries)

**In the schema and migrations**

| Feature | Where | SQLite equivalent | Effort |
|---|---|---|---|
| 14 native `ENUM` types (`autonomy`, `instance_type`, `instance_env`, `decision_status`, `audit_outcome`, `task_status`, `auth_mode`, `decision_kind`, `task_kind`, `task_risk`, `complexity`, `session_role`, `architect_phase`, `message_role`) | 0001, 0003, 0004, 0005 | `sa.Enum(native_enum=False)` gives `VARCHAR` plus `CHECK` | Low |
| `ALTER TYPE … ADD VALUE` | 0005 (4×), 0007 (1×) | On SQLite: rebuild the CHECK in batch mode, or skip it (the CHECK is created final) | Low |
| `ARRAY(Text)` | `projects.aliases`, `projects.checks`, `project_instances.allowed_tools`, `.preapproved_writes`, `tasks.changed_files`, `passkeys.transports` | `JSON` list. **No query uses array operators** (checked: no `any_`, `contains`, `overlap`) | Low |
| `JSONB` | ~15 columns | `JSON`. One query reads into it: `Setting.value["expires"].astext` in `services/owner.py:306` | Low |
| `gen_random_uuid()` server defaults | every UUID primary key | Python-side `default=uuid.uuid4` (works on both) | Low |
| `Identity()` bigint keys | `audit_events`, `session_events`, `routing_examples`, `login_attempts` | `INTEGER PRIMARY KEY` | Low |
| `timestamptz` and `server_default=now()` | everywhere | **Watch out:** SQLite drops time zones, so aware/naive comparisons break. Needs a small `UTCDateTime` TypeDecorator plus Python-side defaults | Medium (subtle) |
| Regex `CHECK (name ~ '…')` | `ck_projects_name_slug`, `ck_instances_name_slug`, `secret_ref` pattern | No `~` in SQLite. Pydantic already validates these, so on SQLite the CHECK is left out | Low |
| Partial indexes (`postgresql_where`) | default binding, unrouted messages, pending voice confirmations | SQLite supports partial indexes (`sqlite_where`) | Low |
| Composite foreign key with type/env | `project_instances` → `instances` | Supported | None |
| `plpgsql` trigger: audit log is append-only | 0003 | SQLite trigger with `RAISE(ABORT, …)` | Low |
| `plpgsql` NOTIFY triggers for live updates | 0007 (session_events, tasks, decisions, messages) | Replaced by an in-process event bus (below) | Medium |

**In queries and runtime code**

| Feature | Where | Desktop replacement |
|---|---|---|
| `LISTEN` / `NOTIFY` / `pg_notify` | `listen.py`, `api/live_routes.py` (SSE), `services/tasks.py`, `threads.py`, `runtime.py`, `decisions.py`, `inbox.py` | One `events` interface. Server mode: Postgres NOTIFY (unchanged). Desktop: in-process pub/sub, since everything is one process |
| `pg_advisory_xact_lock` | `services/owner.py:121` (sign-in pacing) | Not needed: desktop has no sign-in |
| `SELECT … FOR UPDATE` / `SKIP LOCKED` | `tasks.py:147,295,325`, `threads.py:113,148`, `decisions.py:98`, `architect.py:121`, `owner.py:250,265`, `telegram/bridge.py:439,533,587` | SQLite ignores row locks. Use `BEGIN IMMEDIATE` (one writer at a time) on desktop; claims stay correct because there is one process |
| `INSERT … ON CONFLICT` via `dialects.postgresql.insert` | `owner_routes.py`, `examples.py`, `owner.py`, `retention.py`, `revocations.py`, `runtime.py`, `telegram/bridge.py` (7 files) | SQLite has the same API (`dialects.sqlite.insert`). One `upsert()` helper picks the dialect |
| `RETURNING` | `decisions.py`, `examples.py`, `revocations.py` | SQLite ≥ 3.35 supports it (Python 3.12 ships 3.4x) |
| `DISTINCT ON` | `api/work_routes.py:88` (last message per thread) | Rewrite with `ROW_NUMBER()`, which works on both |
| `psycopg` URL rewriting | `listen.py`, `live_routes.py`, `netpolicy.py` (works out the install's own DB address so connections can't point at it) | Event bus. `netpolicy` treats SQLite as "no own address" but still blocks loopback |
| testcontainers Postgres | `tests/conftest.py` | A `RINGKEEPER_TEST_DB=sqlite` switch; Postgres-only tests marked |

**Verdict:** SQLite is reasonable. No feature forces embedded Postgres. The
riskiest item is time zones, which is a correctness bug waiting to happen, so it
gets its own tests. `psycopg` stays in both modes, because the Supabase/Postgres
*connector* talks to client databases with it.

### 2.3 Worker isolation today

Each worker session is a container (`workers/runtime.py`, `worker/`):

- **Filesystem:** sees only its workspace (bind mount) and a per-task state volume. Never the data dir, vault, master key or Docker socket.
- **Network:** joins only the `workers` network, so it reaches the gateway and the internet, not Postgres or the api.
- **Users:** the runner starts as root with 4 capabilities (SETUID, SETGID, KILL, DAC_READ_SEARCH). The agent, git and checks run as uid 1000 (`privileges.py`). `no-new-privileges`, `cap_drop ALL`, memory limit (`worker_memory=2g`).
- **After each run:** `kill_strays` kills anything the agent left running. `gitguard` restores `.git/config`, hooks and info. Nested repos are flagged.
- **Credentials:** connection secrets never enter the worker. It holds only a short-lived signed token for the gateway, which injects secrets on the server side and holds writes for approval. The Anthropic key *is* in the worker's environment (a known risk, in the README).
- **Claude config:** `CLAUDE_CONFIG_DIR` points into the task's state volume, so the agent never sees anyone's personal `~/.claude`.

### 2.4 Where secrets live and how they're read

- `Vault` (`vault/__init__.py`) resolves `secret_ref` strings by scheme: `file://` (AES-GCM file `vault.json` in the data dir, key derived from the master key) and `aws-sm://`.
- The master key comes from `RINGKEEPER_MASTER_KEY` or `data_dir/master.key` (created atomically, `keys.py`). HKDF derives the vault key and the worker-token signing key from it.
- Admin token (`api-token`) and web token (`web-token`) are vault entries. The api copies the web token into a shared volume for the `web` container.
- The DB enforces `secret_ref` with the regex `^(file|aws-sm)://…` in a CHECK (0001).
- Readers: the gateway (connection secrets), the session manager (Anthropic key or OAuth token into the worker env), the Telegram bridge (bot token), the CLI.

### 2.5 What the web login, setup code, 421 and cookies assume

| Mechanism | Assumes | Desktop |
|---|---|---|
| Setup code (`bootstrap.ensure_setup_code`, `setup-code.txt`) | Someone with a terminal on the server proves they own it | **Off.** Whoever opens the app owns it |
| Owner password and passkeys, `/auth/login`, recent confirmation (10 min) | Reachable from other devices (phone over Tailscale) | **Off.** Single local user. The typed-connection-name step for destructive writes **stays** |
| Paced rate limiting, `login_attempts`, advisory lock | Remote attackers guessing passwords | Off (nothing to guess) |
| Host allowlist, 421 (`web/lib/hosts.ts`, `proxy.ts`) | DNS rebinding against a server | **Stays**, enforced by the sidecar: only `127.0.0.1:<port>` and `localhost:<port>` |
| Cookies `rk_session` (httpOnly, SameSite=Strict, Secure on HTTPS) and `rk_csrf` | Next.js server sets them after login | Replaced by one desktop cookie set on launch (§4.5). CSRF header kept |
| Web token useless without a session (`api/auth.py`, `WEB_PUBLIC`) | Separate web container | Desktop caller type: valid only with the launch secret |
| `RINGKEEPER_WEB_ORIGIN`, `TRUST_PROXY`, `X-Forwarded-*` | Reverse proxy (Tailscale Serve) | Not used |
| Admin token for scripts and CLI | Server operators | Kept (CLI still works on desktop for power users) |

### 2.6 The stt and demo profiles

- **stt:** Python 3.12, faster-whisper (CTranslate2), PyAV 18, model `small` (~460 MB) downloaded to a volume on first start. CPU int8, 3 GB memory cap. The api calls it over HTTP for Telegram voice notes and the web app's microphone button. It parses untrusted audio, which is why it's boxed in.
- **demo:** one Postgres 16 with three fictional clients' schemas (`docker/demo-targets/init.sql`), used by the demo and by e2e. Needs Postgres, so it's **dev/server only**. Desktop gets no demo databases; first run creates a real project instead.

### 2.7 Other hidden dependencies

- **git** on the host side: workers branch, commit and diff with the git CLI (`worker/ringkeeper_worker/workspace.py`). A clean Mac has only the `/usr/bin/git` stub, which pops up an "install developer tools" dialog. A clean Windows machine has no git at all. **We must bundle git** (MinGit on Windows ~30 MB; a static git build on macOS).
- **Claude Code on Windows** prefers Git Bash but now falls back to PowerShell when it's missing ([setup docs](https://code.claude.com/docs/en/setup)). Bundled MinGit has no bash, so the agent would use PowerShell on Windows. That's fine, but it's a behaviour difference to test.
- **`claude` CLI:** bundled inside the `claude-agent-sdk` platform wheel (Windows x64, macOS, Linux; [PyPI](https://pypi.org/project/claude-agent-sdk/0.2.131/), [troubleshooting](https://code.claude.com/docs/en/agent-sdk/troubleshooting)). It's the largest single file in the installer (~200 MB uncompressed).
- **Paths:** `workspaces_root` and `daemon_path()` translate host paths for Docker. Desktop uses real local paths.
- **File ownership hand-offs** (`data_uid`, `web_uid`): Docker only.

---

## 3. Workers on the desktop: what isolation we lose and what we keep

Plainly: **a desktop worker is a program running as the user, on the user's computer.**
That's the same trust model as Claude Code or Cursor on a laptop, which these
users may already run. It's weaker than our container.

**We lose:**

1. **Filesystem walls.** The agent can read anything the user can: other client folders, `~/.ssh`, browser profiles, the Ringkeeper data folder including the SQLite file and the encrypted vault. It *can't* read the master key without the keychain (on macOS the keychain asks; on Windows, Credential Manager does **not** stop other programs run by the same user).
2. **Network separation.** It can reach anything on the local network, not just the gateway.
3. **Resource caps.** No hard memory or pid limit by default.
4. **The separate worker user.** No root, no setuid. Git hooks the agent plants would run as the user next time they use git in that folder (gitguard still undoes them after each run, see below).

**We keep:**

- **Connection secrets stay out of the worker.** The gateway still holds them and injects them server-side; the worker gets a short-lived, project-scoped token. This is the most important protection and it carries over fully.
- **Approvals, destructive-write confirmation, audit log, scrubbing.** All in the gateway and core, unchanged.
- **Claude Code's own permission rules** per autonomy level (`workers/manager.py`: strict disallows Bash and web; standard disallows web).
- **gitguard** (restore `.git/config`, hooks, info after each run) and **stray-process cleanup**: a Windows Job Object kills the whole process tree; on macOS, a process group plus `kill`.
- **A private Claude config per task** (`CLAUDE_CONFIG_DIR` in the app data folder), so workers never pick up the user's own `~/.claude` settings, MCP servers or memory.
- **Soft limits:** a Job Object memory cap on Windows, `RLIMIT_AS` on macOS, plus our existing turn and runtime caps.

**Options to claw some back (not in M2, your call):**

- **macOS:** wrap workers in a Seatbelt (`sandbox-exec`) profile: write only to the workspace and state dir, deny reads of `~/.ssh`, `~/Library/Keychains` and the Ringkeeper data folder. Cheap, solid, but Apple marks `sandbox-exec` as deprecated (it still works).
- **Windows:** AppContainer or a restricted token is possible but much more work. Not proposed for v1.
- **"Use Docker for workers if it's installed"**: a desktop setting that reuses `DockerRuntime`. Free to keep since the code exists; for cautious users.

The README "Known risks" section will say this in the same plain terms.

---

## 4. Target architecture

### 4.1 One core, two modes

A single setting, `RINGKEEPER_MODE=server|desktop`, picks implementations at the seams below. Everything else (router, architect, approvals, adapters, gateway logic, tasks, Telegram) is shared code.

| Seam | Server mode (today, unchanged) | Desktop mode |
|---|---|---|
| Database | Postgres via `RINGKEEPER_DATABASE_URL` | SQLite file in the app data folder, WAL mode, `busy_timeout` |
| Events | Postgres LISTEN/NOTIFY | In-process pub/sub |
| Vault master key | `master.key` file or env | OS keychain item `Ringkeeper/master-key` |
| Worker runtime | `DockerRuntime` | `ProcessRuntime` (new, same `ContainerRuntime` protocol) |
| Caller auth | admin token, web token + owner session | admin token (CLI), launch secret (app window) |
| Web UI | Next.js server in `web` container | Static export served by the sidecar |
| STT | `stt` container | Downloadable Voice component, same HTTP protocol, a local subprocess |

### 4.2 Storage interface

SQLAlchemy is already our repository layer: services use the ORM, not raw SQL.
So I won't wrap every query in a new repository class (lots of churn for no gain).
Instead, a small `ringkeeper.storage` module holds the dialect-specific pieces:

- portable column types (`Enum`, `StrList` = ARRAY|JSON, `Doc` = JSONB|JSON, `UTCDateTime`), used by `models.py` and migrations;
- `upsert(model, values, key)`;
- `claim_next(query)` (FOR UPDATE SKIP LOCKED on Postgres, `BEGIN IMMEDIATE` on SQLite);
- `notify(channel, payload)` and `listen(channels)`;
- engine creation (`connect_timeout` for Postgres, `check_same_thread=False` plus pragmas for SQLite).

A lint test fails if any module outside `storage` imports `sqlalchemy.dialects.postgresql`.

### 4.3 Migrations on both databases

- 0001–0008 switch to the portable types. On Postgres they must produce **exactly the same schema as today**. A new test migrates a fresh Postgres with the new chain, compares it to a snapshot of today's schema, and fails on any difference. Existing server installs are already at 0008 and won't re-run them.
- Postgres-only steps (triggers, `ALTER TYPE`, regex CHECKs) are wrapped in `if dialect == "postgresql"`, with a SQLite counterpart where one is needed (append-only audit trigger).
- Alembic runs with `render_as_batch=True` on SQLite (for ALTER TABLE).
- CI: the migration tests run on both databases: upgrade to head, downgrade to base, upgrade again.

### 4.4 Desktop process layout

```
Ringkeeper.app / Ringkeeper.exe  (Tauri: window, tray, updater)
  └─ sidecar: embedded python -m ringkeeper desktop
       ├─ API + static web UI     on 127.0.0.1:<random port A>
       ├─ MCP gateway             on 127.0.0.1:<random port B>
       ├─ session manager, router, (optional) Telegram bridge
       └─ workers: python -m ringkeeper_worker  (one subprocess per session)
            └─ bundled claude CLI, bundled git
  └─ (optional) Voice component: python -m ringkeeper_stt on 127.0.0.1:<port C>
```

- One process for API and gateway (two ports, so worker tokens and app requests never share a listener). Tauri starts it, health-checks it, restarts it if it crashes, and stops it on quit. Closing the window leaves it in the tray, so routines keep running while the computer is on.
- The sidecar writes its ports and a launch secret to a file only the user can read; Tauri reads them.
- Data: `%LOCALAPPDATA%\Ringkeeper` on Windows, `~/Library/Application Support/Ringkeeper` on macOS. Includes `ringkeeper.db`, `vault.json`, logs, worker state. Workspaces default to `Documents/Ringkeeper/<project>`, and the user can pick any folder.

### 4.5 Desktop auth (no login, still locked)

- Bind **127.0.0.1 only**, random ports, host allowlist (421 for anything else), strict `Origin` check on writes.
- On each launch the sidecar makes a random secret. Tauri opens the window at `http://127.0.0.1:A/desktop/start#<secret>`. The page exchanges the secret once for an httpOnly, SameSite=Strict cookie and drops it from the URL. Every API call needs that cookie plus the CSRF header.
- A website in the user's browser can't get the secret, so it can't drive the API (CSRF and DNS rebinding blocked). Other programs running as the same user could read the secret file, but they could read the database anyway; same-user programs are inside the trust boundary on a desktop.
- No setup code, no password, no passkeys, no recent-confirmation prompts. Destructive held writes still need the connection name typed.
- Subscription login stays **off** on desktop: API key only (Anthropic's terms don't allow third-party apps to offer claude.ai login).

### 4.6 Serving the web UI without Node

- `next build` with `output: "export"` when `RINGKEEPER_WEB_TARGET=desktop`; server mode keeps `output: "standalone"`. Same source.
- Three dynamic routes (`projects/[name]`, `tasks/[id]`, `threads/[id]`) can't be statically exported as they are. They become query-string pages (`/projects/view?name=…`) **in both modes**, with redirects from the old URLs in server mode so bookmarks keep working.
- The BFF (`web/app/bff/[...path]/route.ts`) is mounted directly by the sidecar in desktop mode (same paths, same body limits, same CSRF check). The CSP and security headers move into a sidecar middleware for desktop. `proxy.ts` keeps doing them in server mode.
- The alternative is shipping Node plus the standalone Next server as a second sidecar: no web changes, but +40 MB, another process to supervise and another runtime to patch. Not recommended.

### 4.7 STT as a downloadable component

- In the app: "Turn on voice input" → download the Voice component: a separate relocatable Python env with faster-whisper and PyAV (~120 MB), plus a model (`base` ~145 MB or `small` ~460 MB; your call, §8).
- Same `ringkeeper_stt` server code, run as a local subprocess on 127.0.0.1 with its existing magic-byte, duration, size and concurrency guards. The Docker caps become a Job Object or rlimits.
- macOS needs a microphone usage string in `Info.plist`; Windows WebView2 asks for microphone permission in-page.

### 4.8 Connections per project (design for M5)

Today a **connection** (`instances`) is a typed instance (supabase, n8n, neon, vercel, airtable, google) with env (prod/staging/dev) and a vault secret. **Bindings** (`project_instances`) attach it to projects, with allowed tools and preapproved writes. The gateway serves each binding as MCP tools to that project's workers only. Native adapters exist only for **supabase** (any Postgres) and **n8n**.

The "two Neon projects in two Ringkeeper projects, no cross-talk" requirement already matches this model: worker tokens carry the project, and the gateway resolves only that project's bindings. M5 adds the per-project UI and more connectors. The open question is **how** we add connectors (§8, Q1):

- **A. Native adapters** (what we have): full read/write classification, held writes, destructive detection and scrubbing per tool. Each connector is real work (roughly 1–3 days each).
- **B. Proxy official MCP servers through the gateway**: the gateway connects to the vendor's MCP server per connection instance (remote HTTP with an API key or OAuth; or a local stdio server), injects credentials, and re-exposes the tools to workers. Approvals use the tools' `readOnlyHint`/`destructiveHint` annotations plus a per-connector list of known read-only tools. Unknown tools default to "needs approval". Faster to add connectors, coarser control. Local stdio servers are mostly npm packages, which means **shipping Node**.
- **Recommendation:** A for databases (Supabase, Neon: we already have the SQL classifier), B with **remote** MCP servers for SaaS (Vercel, Airtable, n8n's MCP, Twilio), no Node in v1. Either way, the gateway stays the single choke point, so credentials never reach the worker.

**Import from Claude Desktop (M4, optional step):** read `claude_desktop_config.json` (`%APPDATA%\Claude\` or `~/Library/Application Support/Claude/`). For each server: remote URL servers import directly; `npx`/`uvx` servers import only if that tool is already on the machine. `env` values that look like secrets move into the vault and are removed from the stored config. Imported servers run behind the gateway like option B, so approvals still apply.

---

## 5. Recommended stack and the risks I see

**Stack:** Tauri 2 (Rust shell, WebView2 on Windows, WKWebView on macOS) + static Next.js UI + embedded CPython 3.12 (python-build-standalone) running the existing FastAPI/SQLAlchemy core + SQLite + OS keychain (`keyring` package: Credential Manager on Windows, Keychain on macOS) + bundled `claude` CLI (from the SDK wheel) + bundled git.

**Installer size (estimate):** shell ~10 MB, Python + dependencies ~70 MB, `claude` CLI ~70–90 MB compressed, git ~30 MB. Total **~180–200 MB** download. Claude Desktop is in the same range.

**Risks:**

1. **Unsigned builds feel broken to non-technical users.** On macOS, Gatekeeper blocks them outright (the user must allow the app in System Settings → Privacy & Security). Windows shows a SmartScreen "unknown publisher" screen. The 5-minute, zero-terminal goal is only realistic with signing and notarization (§7).
2. **Auto-update needs public download URLs.** The repo is private, and the Tauri updater can't authenticate to private GitHub releases. Options: a public "releases" repo, or a bucket (Cloudflare R2 / S3). §8, Q4.
3. **Time zones on SQLite**: silent wrong comparisons if missed. Mitigated by `UTCDateTime` and dedicated tests.
4. **Windows antivirus** may flag a freshly built, unsigned app that spawns `python.exe` and `claude.exe`. Embedded Python lowers the risk versus PyInstaller; signing lowers it most.
5. **Agent on Windows uses PowerShell** (no bundled bash). Prompts and checks that assume bash may behave differently. Test real tasks on Windows.
6. **SDK and CLI updates** come only through app updates (the CLI is pinned inside the SDK wheel). Fine, but every SDK bump means a new release.
7. **Weaker worker isolation** (§3). Documented, and partly recoverable on macOS.
8. **Third-party connectors via MCP** (if option B): tool annotations are self-reported by the vendor, so approvals for those tools rely on our allowlists and a "default: ask" rule.
9. **Clean-machine testing:** Windows Sandbox (built into your Windows 11 Pro) gives a free clean Windows. A clean Mac needs Apple hardware (a spare Mac, a fresh macOS user account, or a VM on a Mac). §8, Q6.

---

## 6. Milestones (after you approve)

Each one ends with tests passing on both databases, one or more small commits,
and a short plain-English note. I'll ask before pushing.

- **M1. Storage and secrets interfaces.** `ringkeeper.storage` (types, upsert, claim, events), portable migrations with a Postgres schema-snapshot test, `UTCDateTime`, keychain master-key source behind the existing `Vault`, `RINGKEEPER_MODE`. Server mode byte-identical on Postgres. CI gains a `tests (sqlite)` job. *Done when:* all existing tests green on Postgres, and the non-Docker tests green on SQLite.
- **M2. Desktop runtime.** `ringkeeper desktop` command: SQLite, keychain, in-process events, API + gateway on random 127.0.0.1 ports, `ProcessRuntime` (Job Object / process group, private `CLAUDE_CONFIG_DIR`, gitguard, bundled git lookup), launch-secret auth, sidecar-served static UI. *Done when:* a stub-agent task and one real task run from a dev command on Windows and macOS, with no Docker running.
- **M3. Tauri shell.** Window, tray (open, pause workers, quit), starts, stops and restarts the sidecar, survives app restarts and crashes, single instance, data in the standard folder, optional "start at login". *Done when:* kill the sidecar → it comes back; quit → nothing left running.
- **M4. First run in the app.** API key screen (checked live with a free `GET /v1/models` call, no tokens spent) → first project (name and folder) → optional Claude Desktop import (moved to M5, see Progress). *Done when:* fresh data folder → working project with no terminal.
- **M5. Per-project connections UI.** Connect once per project, the gateway serves that project's connections with credentials injected, never asks again. New connectors per your Q1 answer. Explicit no-cross-talk test: two Neon (or Postgres) connections in two projects, each worker sees and touches only its own. *Done when:* that test passes on both databases.
- **M6. Packaging.** `.msi`/`.exe` (NSIS) and `.dmg` built in CI on Windows and macOS runners, Tauri updater with our own update-signing key, unsigned at first. Signing and notarization wired in but switched off until you have the accounts.
- **M7. Docs.** `docs/INSTALL.md` becomes the short desktop page (download, open, paste key, done) with screenshots. The Docker guide moves to `docs/ADVANCED.md`. README points to both.

Then the **definition-of-done test**: clean Windows (Windows Sandbox) and clean Mac, timed, zero terminal.

---

## 7. Code signing and notarization: what I need from you, and roughly what it costs

Not blocking: unsigned builds are fine for testing. Prices are approximate; I'll confirm before M6.

| Platform | What | Approx. cost | What I need |
|---|---|---|---|
| macOS | Apple Developer Program (Developer ID certificate + notarization) | **$99/year** | Enrolment (individual or company; a company needs a D-U-N-S number, which is free but takes days). Then an app-specific password or API key in CI secrets |
| Windows, option 1 | Azure Artifact Signing (formerly Trusted Signing), Basic tier | about **$10/month** | An Azure subscription and identity validation. Eligibility depends on country and whether you sign as a company or an individual ([pricing](https://azure.microsoft.com/pricing/details/artifact-signing/)) |
| Windows, option 2 | OV code-signing certificate on a hardware token or cloud HSM | about **$200–400/year** | Business or identity documents. SmartScreen reputation builds up over time |
| Updates | Tauri update-signing key | free | Nothing. I generate it; you keep a backup |
| CI | GitHub macOS and Windows runners on a **private** repo | macOS minutes count **10×**, Windows **2×** against your plan's free minutes | Your OK on running packaging jobs only on tags or on demand (my suggestion) |

---

### Azure Artifact Signing for General Informatics LLC: eligibility and what's needed

Checked 2026-10-25 against Microsoft's [quickstart](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart) (updated 2026-10-08).

- **Region:** Public Trust certificates for organizations are available in the US, Canada, EU, UK, Australia, New Zealand, Japan, South Korea, Singapore, Switzerland, Norway and Israel. A US LLC qualifies.
- **Business age: the one open risk.** The current docs list no minimum age. A Microsoft moderator confirmed in August 2026 there is "no minimum org age" ([Q&A](https://learn.microsoft.com/en-us/answers/questions/5977141/azure-artifact-signing-trusted-signing-is-a-us-llc)). During the preview, though, organizations needed three years of verifiable tax history, and some younger companies report being declined. If the LLC is under three years old, start validation early. If it fails, the fallback is an OV certificate from a commercial CA.
- **What validation asks for:**
  - the legal name exactly as in public records (keep the state registry up to date);
  - a website on the company's domain;
  - two monitored email addresses on that same domain that accept external links;
  - a business identifier (the EIN);
  - the registered business address;
  - the name, exactly as on a government ID, of the person who does the individual ID check (phone camera, AU10TIX, Microsoft Authenticator).
  - Any extra documents must be dated within the last 12 months; domain registration papers should list the company.
- **Azure setup:**
  - an Entra tenant and an Azure subscription;
  - register the `Microsoft.CodeSigning` provider;
  - an Artifact Signing account (Basic tier) in a supported region, such as East US;
  - your user given the "Artifact Signing Identity Verifier" role.
  - Identity validation is done **only in the Azure portal**.
- **Timing:** validation takes 1–20 business days, and billing starts as soon as the account is created. **Start it when M4 begins**, so it's done before M6. For CI I'll then need a certificate profile name, the account name and endpoint, and a service principal with the "Artifact Signing Certificate Profile Signer" role, stored as GitHub secrets.

## 8. Questions for you (each affects what users see or what it costs)

1. **How do we add connectors?** Native adapters (more control, slower) vs proxying vendors' remote MCP servers (faster, coarser approvals) vs the hybrid I recommend (§4.8). Also: which 3 connectors first after Supabase/Neon?
2. **Gmail.** Google requires app verification and a yearly third-party security assessment (CASA) for Gmail scopes; it typically costs hundreds to a few thousand dollars a year. Alternatives: keep Gmail "bring your own Google Cloud app" (hard for non-technical users) or leave it for later. Which?
3. **Secrets:** OK with "master key in the keychain, secrets in the encrypted vault file" (§1), or do you want every secret as its own keychain item?
4. **Update hosting:** a public `ringkeeper-releases` GitHub repo (free) or a storage bucket (a few dollars a month)?
5. **Voice model size:** `base` (~145 MB, faster, less accurate) or `small` (~460 MB, today's default)?
6. **Testing on a clean Mac:** do you have a Mac I can use (a fresh user account works), or should the DoD test on macOS be yours to run from my checklist?
7. **macOS worker sandbox** (§3, Seatbelt): include it in M2, or leave it for later?
8. **Workers keep running in the tray** when the window is closed (so routines run while the computer is on): OK as the default?

---

## 9. Later, not now: where automatic dispatch and model selection hook in

Parts of both already exist; these are the seams to extend, nothing to build now.

- **Task dispatch to projects:** `router/service.py` already routes a message to a project with a confidence threshold (`router_confidence_threshold`) and asks when unsure (`candidates()`, `_why_asking()`). Automatic dispatch means lowering "ask" for trusted cases, plus a scheduler for routines (desktop: in the sidecar while the app runs; server: the api service).
- **Model selection:** `model_choice.py` (`session_model()`, `tier()`) picks the model by role and task complexity and steps up after failed check rounds; per-project overrides live in `projects.models`. Automatic selection would replace `tier()`'s fixed table with a cost/quality policy.
