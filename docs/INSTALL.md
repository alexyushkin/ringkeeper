# Installing Ringkeeper

> **On Windows, most people want the desktop app instead:** one installer, no
> Docker, no terminal. See [Install](../README.md#install) in the README. This
> guide is for the server install (Docker), for advanced setups.

This guide takes you from nothing to a working Ringkeeper in about 30 minutes,
most of it waiting for downloads. You copy and paste commands; no programming
needed. It covers:

1. [Before you start](#1-before-you-start): what you need
2. [Install on your computer](#2-install-on-your-computer) (Windows, Mac or Linux)
3. [Use it from your phone](#3-use-it-from-your-phone)
4. [Install on a cloud server](#4-install-on-a-cloud-server) instead
5. [Everyday tasks](#5-everyday-tasks): stop and start, update, back up and restore, forgotten password, uninstall
6. [Troubleshooting](#6-troubleshooting)

Commands are shown for **Windows** (PowerShell) and for **Mac / Linux**
(Terminal) where they differ. When only one is shown, it works on all three.
Run every `docker compose` command from inside the `ringkeeper` folder.

---

## 1. Before you start

### Your computer

- **Windows 10 or 11** (64-bit), **macOS** (one of the three latest versions),
  or **Linux**.
- **8 GB of memory** at least (16 GB is better), and **15 GB of free disk**.
- On Windows, virtualization must be on (it usually is; Docker Desktop tells
  you if it isn't).

### Software to install

| | Windows | Mac | Linux (Ubuntu) |
| --- | --- | --- | --- |
| Docker | [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) (keep "Use WSL 2" on) | [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) | [Docker Engine](https://docs.docker.com/engine/install/ubuntu/) |
| Git | `winget install Git.Git` | `xcode-select --install` | `sudo apt install git` |

Then, in Docker Desktop's **Settings**:

- **General → Start Docker Desktop when you sign in**: on. Otherwise
  Ringkeeper is off after every restart until you open Docker Desktop.
- **Resources → Memory**: at least 6 GB.

Docker Desktop is free for personal use and small businesses (under 250
people and under $10 million a year); larger companies need a paid plan.

### Accounts and keys

Have these ready; the setup wizard asks for them.

1. **Ringkeeper's code.** It's public on GitHub, so `git clone` needs no
   account. (Or download the ZIP from the repository page: **Code → Download
   ZIP**.)
2. **An Anthropic API key** (required). Ringkeeper's assistants run on
   Claude:
   1. Go to [platform.claude.com](https://platform.claude.com) and sign up.
   2. **Settings → Billing**: add credit. Keys don't work without it.
   3. **Settings → Limits**: set a monthly spend limit you're comfortable
      with.
   4. **Settings → API keys → Create key**. Copy the key (it starts with
      `sk-ant-`). It's shown only once.
3. **A Telegram bot** (optional, for approvals and questions on your phone):
   1. In Telegram, open **@BotFather** and send `/newbot`.
   2. Choose a display name, then a username ending in `bot`.
   3. Copy the token it gives you (like `123456789:AA...`).
   4. Open your new bot and press **Start**.
4. **Your first connection's secret** (the wizard ends by adding one):
   - **Supabase**: in your Supabase project, click **Connect** and copy the
     **Session pooler** connection string (it starts with `postgresql://`).
     Replace `[YOUR-PASSWORD]` with the database password. Use the pooler,
     not "Direct connection", which often doesn't work from Docker.
   - **n8n**: your n8n address (like `https://n8n.example.com`), and an API
     key from **Settings → n8n API → Create an API key**.
5. **Tailscale** (only to use Ringkeeper from your phone or on a cloud
   server): a free account at [tailscale.com](https://tailscale.com).

---

## 2. Install on your computer

### Step 1: get the code

Open PowerShell (Windows) or Terminal (Mac/Linux) and go to the folder where
you want Ringkeeper, then:

```bash
git clone https://github.com/alexyushkin/ringkeeper.git
cd ringkeeper
```

(Windows PowerShell shows git's normal progress, like "Cloning into
'ringkeeper'...", in red. That's not an error. The same goes for Docker's
progress lines later.)

### Step 2: settings

Create your settings file from the example:

```bash
cp .env.example .env
```

Open it in a text editor:

- Windows: `notepad .env`
- Mac: `open -e .env` (or `nano .env`; in TextEdit, turn off smart quotes)
- Linux: `nano .env`

Change two things at the top, under **MUST SET**:

1. **`POSTGRES_PASSWORD`**: replace the placeholder with a random password of
   letters and digits only. To make one:
   - Windows: `-join ((48..57)+(97..122) | Get-Random -Count 32 | % {[char]$_})`
   - Mac/Linux: `openssl rand -hex 24`

   Set it now: the database keeps the password it was first started with.
2. **`RINGKEEPER_WORKSPACES_ROOT`**: the folder where your client projects
   live, one subfolder per project. Use forward slashes and no quotes, for
   example `C:/Users/Jane/Clients` (Windows) or `/Users/jane/Clients` (Mac).
   On Windows, don't use a folder that OneDrive syncs.

Also check **`RINGKEEPER_STT_LANGUAGE`** (`en` by default): the language you'll
speak to Ringkeeper in, for voice requests. Leave everything else as it is
and save the file.

Then create the workspaces folder, with a subfolder for your first project:

- Windows: `mkdir C:/Users/Jane/Clients/acme-web`
- Mac/Linux: `mkdir -p ~/Clients/acme-web`

Put the project's files in its subfolder (for code, clone the project's
repository there: `git clone <its address> acme-web`). Ringkeeper works on a
separate git branch per task and never pushes, so a git repository is best.

### Step 3: start Ringkeeper

```bash
docker compose --profile worker build worker
docker compose --profile stt up -d --build
```

- The first command builds the image that tasks run in.
- The second starts everything. Leave out `--profile stt` if you don't want
  voice requests (it saves about 1.5 GB).
- **The first time takes 10 to 20 minutes** (downloads and builds). Later
  starts take seconds.

Check that it's running:

```bash
docker compose ps
```

You should see `postgres`, `api`, `gateway`, `web` (and `stt`) with
`Up ... (healthy)`. Right after the start, `stt` shows `(health: starting)`
for a few minutes while it downloads its speech model; everything else works
meanwhile.

### Step 4: the setup wizard

Get your one-time setup code (two groups of four characters, like
`7J7M-CDD6`):

```bash
docker compose exec api ringkeeper web setup-code
```

Open **http://localhost:3000** in your browser. The wizard asks for:

1. **The setup code**, and a password for your login (at least 12
   characters). Then **download the recovery key** and keep it somewhere
   safe, like a password manager. If you lose it, you'll need to re-enter
   your connection secrets.
2. **Your Anthropic API key.** It's checked, then stored encrypted.
3. **Telegram** (or **Skip for now**): paste the bot token, send your bot the
   code the app shows, and pick your chat when it appears.
4. **Your first project**: the client, a name, a short description, and its
   **Folder**: the subfolder's name from step 2 (`acme-web`).
5. **Your first connection**: Supabase or n8n, "Production" or "Test", and
   its secret. Press **Test connection**, then finish.

Done. Ask for something in plain words on the Home screen.

---

## 3. Use it from your phone

Your phone needs an HTTPS address for the app (browsers only allow the
microphone, installing the app and passkeys over HTTPS). Tailscale gives you
one that only your own devices can reach.

1. Install Tailscale on this computer and sign in:
   - Windows: `winget install Tailscale.Tailscale`, then sign in from the
     tray icon.
   - Mac: install it from the App Store, sign in, then in its menu bar menu
     choose **Install CLI** (so the `tailscale` command works in Terminal).
   - Linux: `curl -fsSL https://tailscale.com/install.sh | sh` then
     `sudo tailscale up`.
2. In the Tailscale admin console, open
   [DNS](https://login.tailscale.com/admin/dns) and turn on **MagicDNS** and
   **HTTPS Certificates**.
3. Give the app its HTTPS address (on Linux, put `sudo` in front):

   ```bash
   tailscale serve --bg --https=443 http://127.0.0.1:3000
   tailscale serve status
   ```

   If it prints a link to enable Serve for your tailnet, open it, approve,
   and run the first command again. `status` shows your address, like
   `https://jane-laptop.tail1234.ts.net`.
4. In `.env`, delete the `#` in front of these two lines and put in your
   address:

   ```
   RINGKEEPER_WEB_ORIGIN=https://jane-laptop.tail1234.ts.net
   RINGKEEPER_WEB_TRUST_PROXY=1
   ```

   Then apply it: `docker compose --profile stt up -d`. The app only answers
   on `localhost` and this address: reached any other way, it says the
   address isn't one it answers on.
5. On your phone, install the **Tailscale** app, sign in to the same account,
   and keep it switched on.
6. Open your address in Safari (iPhone) or Chrome (Android) and sign in. The
   first visit can take a few seconds while the certificate is issued.
7. Install the app: **Share → Add to Home Screen** (iPhone) or **⋮ → Install
   app** (Android).

The phone reaches Ringkeeper only while this computer is on and awake. For an
always-on Ringkeeper, use a cloud server (next section).

---

## 4. Install on a cloud server

The same Ringkeeper on a small virtual server, reachable from your laptop and
phone through Tailscale. No port is open to the internet. (A public address
with your own domain, for client installs, comes in a later version.)

### Step 1: a server

Create a virtual server with any provider (Hetzner, DigitalOcean, AWS
Lightsail, ...):

- **Ubuntu 24.04 or 26.04 LTS**
- **2 CPUs, 4 GB memory** (8 GB with voice requests), **40 GB disk**
- In the provider's firewall (or "security group"), allow only **SSH (port
  22)** inbound.

Name it something neutral (not after a client): with HTTPS, Tailscale
machine names appear in public certificate logs.

Connect to it from your computer (this works in PowerShell too):

```bash
ssh root@<server address>
```

### Step 2: Docker, git and a user

If you logged in as `root`, create a normal user first and log in as that
user (`ubuntu` on many providers already exists):

```bash
adduser ringkeeper
usermod -aG sudo ringkeeper
su - ringkeeper
```

Install Docker Engine by following
[Docker's Ubuntu guide](https://docs.docker.com/engine/install/ubuntu/)
("Install using the apt repository"), then let your user run it, and install
git:

```bash
sudo usermod -aG docker $USER
sudo apt install -y git
exit
```

Log in again (`ssh`, or `su - ringkeeper`) so the group change applies.

### Step 3: Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

It prints a link: open it on your computer and sign in. Then, in the Tailscale
admin console:

- [DNS](https://login.tailscale.com/admin/dns): turn on **MagicDNS** and
  **HTTPS Certificates**.
- [Machines](https://login.tailscale.com/admin/machines): on the server's
  **⋯** menu, choose **Disable key expiry** (otherwise it drops off after
  about six months).

```bash
sudo tailscale serve --bg --https=443 http://127.0.0.1:3000
tailscale serve status    # your address, like https://ringkeeper-1.tail1234.ts.net
```

If it prints a link to enable Serve, open it and run the command again.

### Step 4: Ringkeeper

```bash
sudo mkdir -p /srv/ringkeeper/workspaces
sudo chown -R 1000:1000 /srv/ringkeeper/workspaces
git clone https://github.com/alexyushkin/ringkeeper.git
cd ringkeeper
cp .env.example .env
nano .env
```

In `.env`, set (Ctrl+O, Enter to save; Ctrl+X to quit):

```
POSTGRES_PASSWORD=<openssl rand -hex 24 makes one>
RINGKEEPER_WORKSPACES_ROOT=/srv/ringkeeper/workspaces
RINGKEEPER_WEB_ORIGIN=https://ringkeeper-1.tail1234.ts.net
RINGKEEPER_WEB_TRUST_PROXY=1
RINGKEEPER_MAX_CONCURRENT_WORKERS=1
```

(`RINGKEEPER_MAX_CONCURRENT_WORKERS=1` on a 4 GB server; 2 on 8 GB.)

Put each project's files in its own folder under
`/srv/ringkeeper/workspaces` (for example
`git clone <its address> /srv/ringkeeper/workspaces/acme-web`), owned by user
1000: after cloning, run `sudo chown -R 1000:1000 /srv/ringkeeper/workspaces`
again.

Start it (10 to 20 minutes the first time):

```bash
docker compose --profile worker build worker
docker compose --profile stt up -d --build
docker compose ps
docker compose exec api ringkeeper web setup-code
```

### Step 5: the wizard

On your computer or phone, with Tailscale on, open your address and follow
the wizard as in [section 2, step 4](#step-4-the-setup-wizard). To install the
app on your phone, see [section 3](#3-use-it-from-your-phone), steps 5 to 7.

---

## 5. Everyday tasks

Run these in the `ringkeeper` folder. If you didn't install voice, leave out
`--profile stt` in the start commands. `--profile "*"` means "all parts of
Ringkeeper, including voice"; without it, stop and uninstall would leave the
voice service running.

### Stop and start

```bash
docker compose --profile "*" stop       # stop (data is kept)
docker compose --profile stt up -d      # start again
```

After editing `.env`, run the start command: it applies the change.
`docker compose restart` doesn't re-read `.env`. Settings you change in the
web app's Settings screen are stored in the database and take priority over
`.env`.

### Update to a new version

Back up first (below), then:

```bash
git pull
docker compose --profile worker build worker
docker compose --profile stt up -d --build
docker image prune -f        # removes old versions' images to free disk
```

Ringkeeper is down for about a minute. Database changes apply on start.

### Back up

Three things: the database, the `.ringkeeper` data folder (stored secrets,
encrypted) and `.env`. Keep your recovery key separately, in your password
manager.

```bash
docker compose exec -T postgres pg_dump -U ringkeeper -Fc -f /tmp/ringkeeper.dump ringkeeper
docker compose cp postgres:/tmp/ringkeeper.dump ./ringkeeper-backup.dump
```

Then the data folder and settings:

- Windows: `Compress-Archive -Path .ringkeeper, .env -DestinationPath ringkeeper-backup.zip -Force`
- Mac/Linux: `sudo tar czf ringkeeper-backup.tgz .ringkeeper .env`

Copy `ringkeeper-backup.dump` and the zip (or tgz) **somewhere else**: a backup
next to the original doesn't help if the disk or server is lost. On a server,
your project folders matter too: Ringkeeper never pushes its branches, so
they exist only there.

### Restore

On a fresh install (sections 2 or 4, up to and including step 2 "settings"):

1. Unpack the backup into the `ringkeeper` folder, replacing `.env` and
   `.ringkeeper`:
   - Windows: `Expand-Archive ringkeeper-backup.zip -DestinationPath . -Force`
   - Mac/Linux: `sudo tar xzf ringkeeper-backup.tgz`
2. Start only the database and load the backup:

   ```bash
   docker compose up -d --wait postgres
   docker compose cp ./ringkeeper-backup.dump postgres:/tmp/ringkeeper.dump
   docker compose exec -T postgres pg_restore -U ringkeeper -d ringkeeper --no-owner /tmp/ringkeeper.dump
   ```

3. Start everything: `docker compose --profile stt up -d`. Your login,
   projects, connections and history are back; no setup wizard.

If `master.key` is missing from the restored `.ringkeeper` folder, open your
recovery key file, copy the key line (the long line of letters and digits),
and put it in `.env` as `RINGKEEPER_MASTER_KEY=<the key>` (delete the `#`).

### Forgot your password

```bash
docker compose exec api ringkeeper web reset-login
```

It removes the login (and passkeys), signs out every browser, gives the web
app a new token and prints a new setup code. Open the app and create the
login again; your projects, connections and history stay.

### Change the database password

If the api's log says the database password is weak or the default, set a
new one. Pick 32 random letters and digits, then (put yours in place of
`NEW-PASSWORD`):

```bash
docker compose exec postgres psql -U ringkeeper -d ringkeeper -c "ALTER USER ringkeeper PASSWORD 'NEW-PASSWORD'"
```

Put the same password in `.env` as `POSTGRES_PASSWORD=NEW-PASSWORD` and
start again: `docker compose --profile stt up -d`.

### Lost the recovery key

As long as Ringkeeper still runs, download it again: **Settings → Recovery key
→ Download it again** (it may ask for your password first).

### Uninstall

```bash
docker compose --profile "*" down       # remove the app, keep the data
docker compose --profile "*" down -v    # remove the app AND all its data (cannot be undone)
```

Then delete the `ringkeeper` folder, and the images with
`docker image rm ringkeeper-worker ringkeeper-web ringkeeper-stt`.

---

## 6. Troubleshooting

To see what a part of Ringkeeper is doing: `docker compose logs api` (or
`web`, `gateway`, `stt`); add `-f` to keep watching, Ctrl+C to stop.

| What you see | What to do |
| --- | --- |
| `error during connect` / `Cannot connect to the Docker daemon` | Docker Desktop isn't running: open it and wait until it says it's running. |
| `port is already allocated` | Another program uses that port (often 3000 or 8000). In `.env`, delete the `#` in front of the matching `RINGKEEPER_..._PORT` line and pick another number (for example `RINGKEEPER_WEB_PORT=3010`), then start again. If you change `RINGKEEPER_WEB_PORT`, open that port instead of 3000, and use it in `tailscale serve`. |
| You want a second Ringkeeper on the same machine (a test install, say) | In the second one's `.env`, set its own `COMPOSE_PROJECT_NAME` (for example `ringkeeper2`), its own `RINGKEEPER_WORKER_NETWORK` (`ringkeeper2_workers`) and different ports (see `.env.example`). Without its own project name, its commands control the first install. Keep both on the same version: they share the `ringkeeper-web`, `ringkeeper-stt` and `ringkeeper-worker` images. |
| `dependency failed to start: container ... api ... is unhealthy` | Run `docker compose logs api`. `password authentication failed` means the database was first started with a different `POSTGRES_PASSWORD`: put the old one back, or, on a fresh install with nothing to keep, run `docker compose down -v` and start again. |
| `localhost:3000` doesn't load | Run `docker compose ps`: `web` should be `Up`. If it was just started, wait a minute. |
| The app says "can't find Ringkeeper's API token yet" | The api is still starting: wait a minute and reload. |
| The setup code isn't accepted | Run `docker compose exec api ringkeeper web setup-code` again and use the code it prints (letters aren't case-sensitive; the dash is optional). After 5 wrong tries, wait 15 minutes. |
| `setup-code` says setup is done, but you never finished | A login exists from an earlier try: run `docker compose exec api ringkeeper web reset-login`. |
| "Too many tries" at sign-in | Wait a few seconds and try again (after many wrong passwords, one try every few seconds is allowed). Behind your own proxy with `RINGKEEPER_WEB_TRUST_PROXY=1`, a device with 5 wrong tries waits 15 minutes. Forgot the password? See [Forgot your password](#forgot-your-password). |
| "This address isn't one Ringkeeper answers on" | Set `RINGKEEPER_WEB_ORIGIN` in `.env` to the address you open (for Tailscale, your `https://….ts.net` address), or list other names in `RINGKEEPER_WEB_ALLOWED_HOSTS`, then start again. |
| The api's log warns that the database password is weak | See [Change the database password](#change-the-database-password). |
| A project's **Folder** isn't accepted | Give the name of a folder inside your workspaces folder, like `acme-web` or `clients/acme-web`: no full paths and no `..`. |
| **Test connection** says the address isn't allowed | Connections can't point at Ringkeeper's own services, at `localhost` or at cloud metadata addresses. For a database on this same machine during development, set `RINGKEEPER_ALLOW_LOCAL_CONNECTIONS=1` in `.env`. |
| The Anthropic key isn't accepted | Check that it's an API key from platform.claude.com (it starts with `sk-ant-`, and not `sk-ant-oat`, which is a subscription token), that the account has credit, and that the key wasn't deleted in the console. If Anthropic says the key isn't scoped to a workspace, fill in **Workspace ID** next to the key (platform.claude.com, Settings › Workspaces). |
| **Test connection** fails | Read the message: a wrong password, a wrong address, or (Supabase) use the **Session pooler** string, not "Direct connection". |
| A task fails with "workspace_path ... RINGKEEPER_WORKSPACES_ROOT isn't set" | Set `RINGKEEPER_WORKSPACES_ROOT` in `.env` (without the `#`), then `docker compose --profile stt up -d`. |
| A task fails because a folder doesn't exist | Create the project's folder inside your workspaces folder, with the same name as the project's **Folder** in the app. |
| Voice: "Voice isn't set up on this install" | Start with `--profile stt`. Voice works once `docker compose ps` shows `stt` as `(healthy)`. |
| Voice: the transcript is in the wrong language | Set `RINGKEEPER_STT_LANGUAGE` in `.env` (for example `en`), then start again. |
| The phone can't open the address | Is Tailscale switched on on the phone, and is the computer on and awake? Run `tailscale serve status` on the computer. |
| The microphone or "Install app" is missing on the phone | Use the `https://...ts.net` address, not an `http://` one. |
