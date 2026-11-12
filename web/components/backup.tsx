"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, Field, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { canPickFolder, pickFolder } from "@/lib/client/shell";

interface Info {
  default_folder: string;
  auto: { enabled: boolean; folder: string; keep: number; last: string | null; last_error: string | null };
}

interface Preview {
  created_at: string;
  app_version: string;
  clients: string[];
  projects: { name: string; client: string; folder: string | null; folder_here: boolean }[];
  connections: { name: string; client: string; type: string }[];
  secrets: number;
  agents: number;
  routines: number;
  tasks: number;
  restarting?: boolean;
}

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Settings › Backup (desktop): one password-protected file with everything, restoring
 * it on another computer, and weekly automatic backups. */
export function BackupSettings() {
  const info = useApi<Info>("desktop/backup");
  if (!info.data) return null;
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        A backup is one file with your clients, projects, connections and their keys, agents, routines, settings and history, locked with a password you choose. Project folders aren&apos;t in it: they stay where they are, and a restored project points to the same folder path.
      </p>
      <Export defaultFolder={info.data.default_folder} />
      <Restore />
      <Automatic info={info.data} onSaved={info.reload} />
    </div>
  );
}

function FolderField({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder: string }) {
  const [browse, setBrowse] = useState(false);
  useEffect(() => setBrowse(canPickFolder()), []);
  return (
    <div className="flex items-end gap-2">
      <div className="min-w-0 flex-1">
        <Field label="Folder" value={value} onChange={onChange} placeholder={placeholder} />
      </div>
      {browse ? (
        <Button
          onClick={async () => {
            const picked = await pickFolder("Choose the folder for backups", value || placeholder);
            if (picked) onChange(picked);
          }}
        >
          Browse…
        </Button>
      ) : null}
    </div>
  );
}

function Export({ defaultFolder }: { defaultFolder: string }) {
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setError(null);
    setSaved(null);
    if (password !== again) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    try {
      const done = await api<{ path: string }>("desktop/backup/export", { json: { password, folder: folder || null } });
      setSaved(done.path);
      setPassword("");
      setAgain("");
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card anchor="backup-make" className="space-y-3">
      <h2 className="font-semibold">Make a backup</h2>
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint="At least 8 characters. Without it, the backup can't be opened, by anyone." />
        <Field label="Password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" />
      </div>
      <FolderField value={folder} onChange={setFolder} placeholder={defaultFolder} />
      <Button kind="primary" busy={busy} disabled={password.length < 8 || !again} onClick={save}>
        Save the backup
      </Button>
      {saved ? (
        <Alert tone="good">
          Saved to <span className="font-mono text-xs break-all">{saved}</span>. Keep it somewhere safe, and the password apart from it.
        </Alert>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

function Restore() {
  const [file, setFile] = useState<File | null>(null);
  const [password, setPassword] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send(path: string): Promise<Preview | null> {
    if (!file) return null;
    setError(null);
    setBusy(path);
    try {
      return await api<Preview>(`desktop/backup/${path}`, { method: "POST", body: file, contentType: "application/octet-stream", headers: { "x-backup-password": password } });
    } catch (err) {
      setError(message(err));
      return null;
    } finally {
      setBusy(null);
    }
  }

  const missing = (preview?.projects ?? []).filter((p) => p.folder && !p.folder_here);
  return (
    <Card anchor="backup-restore" className="space-y-3">
      <h2 className="font-semibold">Restore a backup</h2>
      <p className="text-sm text-muted">On a new computer, or to go back. You&apos;ll see what comes in before anything changes.</p>
      <label className="block text-sm font-medium">
        Backup file
        <input
          type="file"
          accept=".rkbackup"
          className="mt-1 block w-full text-sm"
          onChange={(event) => {
            setFile(event.target.files?.[0] ?? null);
            setPreview(null);
          }}
        />
      </label>
      <Field label="Its password" type="password" value={password} onChange={setPassword} autoComplete="off" />
      {!preview ? (
        <Button busy={busy === "preview"} disabled={!file || !password} onClick={async () => setPreview(await send("preview"))}>
          Show what&apos;s in it
        </Button>
      ) : null}
      {preview ? (
        <div className="space-y-2 rounded-xl bg-sunken p-3 text-sm">
          <p>
            Made {new Date(preview.created_at).toLocaleString()} by Ringkeeper {preview.app_version}.
          </p>
          <p>
            {preview.clients.length} client(s), {preview.projects.length} project(s), {preview.connections.length} connection(s) with {preview.secrets} saved key(s), {preview.agents} agent(s), {preview.routines} routine(s), {preview.tasks} task(s) of history.
          </p>
          {preview.projects.length ? (
            <ul className="list-disc space-y-1 pl-5">
              {preview.projects.map((p) => (
                <li key={p.name}>
                  {p.client} › {p.name}
                  {p.folder ? (
                    <span className={`ml-1 text-xs ${p.folder_here ? "text-muted" : "text-warn"}`}>
                      {p.folder_here ? `(folder found: ${p.folder})` : `(folder not on this computer: ${p.folder})`}
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {missing.length ? <Alert tone="warn">Copy the missing project folders to this computer, then set each project&apos;s folder on its page.</Alert> : null}
          <Alert tone="warn">Restoring replaces everything Ringkeeper has on this computer with the backup. What&apos;s here now is kept beside it (ringkeeper.db.before-restore). Ringkeeper restarts to finish.</Alert>
          {done ? (
            <Alert tone="good">Restored. Ringkeeper is restarting; this window comes back in a moment.</Alert>
          ) : (
            <div className="flex flex-wrap gap-2">
              <Button kind="danger" busy={busy === "restore"} onClick={async () => setDone(Boolean(await send("restore")))}>
                Restore and restart
              </Button>
              <Button kind="ghost" onClick={() => setPreview(null)}>
                Cancel
              </Button>
            </div>
          )}
        </div>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

function Automatic({ info, onSaved }: { info: Info; onSaved: () => void }) {
  const auto = info.auto;
  const [enabled, setEnabled] = useState(auto.enabled);
  const [folder, setFolder] = useState(auto.folder);
  const [keep, setKeep] = useState(String(auto.keep));
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api("desktop/backup/auto", { method: "PUT", json: { enabled, folder, keep: Number(keep) || 4, password: password || null } });
      setPassword("");
      setNotice(enabled ? "Saved. The first one is made in a minute or so, then every week." : "Automatic backups are off.");
      onSaved();
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card anchor="backup-auto" className="space-y-3">
      <h2 className="font-semibold">Automatic backups</h2>
      <Toggle label="Make a backup every week" checked={enabled} onChange={setEnabled} hint="Into a folder you choose, like one your cloud drive copies. Older ones are deleted, keeping the last few." />
      {enabled ? (
        <>
          <FolderField value={folder} onChange={setFolder} placeholder={info.default_folder} />
          <div className="grid gap-3 sm:grid-cols-2">
            <Field label="Keep the last" type="number" value={keep} onChange={setKeep} hint="Between 1 and 52 backups." />
            <Field label={auto.enabled ? "New password (leave empty to keep it)" : "Password for these backups"} type="password" value={password} onChange={setPassword} autoComplete="new-password" hint="Kept in Ringkeeper's vault on this computer, so it can lock each backup. Write it down: you need it to restore." />
          </div>
        </>
      ) : null}
      <Button kind="primary" busy={busy} onClick={save}>
        Save
      </Button>
      {auto.last ? <p className="text-xs text-muted">Last one: {new Date(auto.last).toLocaleString()}.</p> : null}
      {auto.last_error ? <Alert>{auto.last_error} Check the folder, then save again.</Alert> : null}
      {notice ? <Alert tone="good">{notice}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}
