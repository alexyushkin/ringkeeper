"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Details, Field, Select, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { canPickFolder, pickFolder } from "@/lib/client/shell";
import { AUTONOMY, slugify } from "@/lib/client/words";

export interface ProjectData {
  name: string;
  owner: string;
  description: string;
  aliases: string[];
  repo_url: string | null;
  workspace_path: string | null;
  autonomy: string;
  checks: string[];
  models: Record<string, string>;
}

/** What adding a project answers. On the desktop: the folder it got. */
export interface ProjectSaved {
  name: string;
  folder?: string;
  created?: boolean;
  git?: boolean;
}

const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

/** Create (no `existing`) or edit a project. */
export function ProjectForm({ existing, onSaved }: { existing?: ProjectData; onSaved: (name: string, saved?: ProjectSaved) => void }) {
  const [client, setClient] = useState(existing?.owner ?? "");
  const [title, setTitle] = useState(existing?.name ?? "");
  const [description, setDescription] = useState(existing?.description ?? "");
  const [aliases, setAliases] = useState((existing?.aliases ?? []).join(", "));
  const [folder, setFolder] = useState(existing?.workspace_path ?? "");
  const [repo, setRepo] = useState(existing?.repo_url ?? "");
  const [autonomy, setAutonomy] = useState(existing?.autonomy ?? "standard");
  const [checks, setChecks] = useState((existing?.checks ?? []).join("\n"));
  const [models, setModels] = useState(
    Object.entries(existing?.models ?? {})
      .map(([k, v]) => `${k}=${v}`)
      .join("\n"),
  );
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = existing ? existing.name : slugify(title);
  // Desktop: any folder on this computer; left empty, a new one in the workspaces folder.
  const root = useApi<{ workspaces_root?: string | null }>(DESKTOP && !existing ? "setup/status" : null).data?.workspaces_root;
  const newFolder = root && name ? `${root}${root.includes("\\") ? "\\" : "/"}${name}` : "";
  const [browse, setBrowse] = useState(false);
  useEffect(() => setBrowse(canPickFolder()), []);

  async function chooseFolder() {
    setError(null);
    try {
      const picked = await pickFolder("Choose the project's folder", folder.trim() || root || undefined);
      if (picked) setFolder(picked);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const body = {
      owner: slugify(client) || client,
      description,
      aliases: aliases.split(",").map((a) => a.trim()).filter(Boolean),
      workspace_path: folder.trim() || null,
      repo_url: repo.trim() || null,
      autonomy,
      checks: lines(checks),
      models: Object.fromEntries(lines(models).map((l) => l.split("=").map((p) => p.trim()) as [string, string])),
    };
    try {
      if (existing) {
        await api(`projects/${existing.name}`, { method: "PATCH", json: body });
        onSaved(name);
      } else {
        onSaved(name, await api<ProjectSaved>("projects", { json: { name, ...body } }));
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-4">
      <Field label="Client" value={client} onChange={setClient} placeholder="Acme Inc." hint="Who this work is for. Projects of different clients never share a task." required />
      {existing ? null : (
        <Field
          label="Project name"
          value={title}
          onChange={setTitle}
          placeholder="Acme website"
          hint={name ? `Short name: ${name}` : "Lowercase letters, numbers and dashes are used as its short name."}
          required
        />
      )}
      <TextArea label="What is it?" value={description} onChange={setDescription} rows={2} hint="One or two sentences. This helps Ringkeeper send requests to the right project." />
      <Field label="Other names people use" value={aliases} onChange={setAliases} placeholder="acme site, the portal" hint="Separate with commas." />
      {DESKTOP ? (
        <div className="space-y-1">
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Field label="Folder" value={folder} onChange={setFolder} placeholder={newFolder} />
            </div>
            {browse ? <Button onClick={chooseFolder}>Browse…</Button> : null}
          </div>
          <p className="text-xs text-muted">
            {existing ? "Any folder on this computer. Ringkeeper makes it if it's missing." : "Any folder on this computer. Leave it empty for a new folder named after the project."}
          </p>
        </div>
      ) : (
        <Field label="Folder" value={folder} onChange={setFolder} placeholder="acme-web" hint="Where the project's files are, inside Ringkeeper's workspaces folder." />
      )}
      <Select
        label="How independently it works"
        value={autonomy}
        onChange={setAutonomy}
        options={Object.entries(AUTONOMY).map(([k, [label, help]]) => [k, `${label}: ${help}`])}
      />
      <Details>
        <div className="space-y-4">
          <Field label="Repository (optional)" value={repo} onChange={setRepo} placeholder="https://github.com/acme/web" />
          <TextArea label="Checks to run after each change" value={checks} onChange={setChecks} hint="One shell command per line, e.g. python -m pytest -q" />
          <TextArea label="Model overrides" value={models} onChange={setModels} hint="One per line: architect=…, worker=…, low=…, medium=…, high=…" />
        </div>
      </Details>
      {error ? <Alert>{error}</Alert> : null}
      <Button type="submit" kind="primary" busy={busy} disabled={!client || !name}>
        {existing ? "Save" : "Add project"}
      </Button>
    </form>
  );
}

export interface ConnectionData {
  name: string;
  type: string;
  env: string;
  owner: string;
  base_url: string | null;
  config: { preview?: string; [key: string]: unknown };
  secret_stored: boolean;
  projects: string[];
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_message: string | null;
}

export { ConnectionForm, SERVICES } from "@/components/connection-form";

export function TestButton({ name, onDone }: { name: string; onDone?: (ok: boolean) => void }) {
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      const outcome = await api<{ ok: boolean; message: string }>(`connections/${name}/test`, { method: "POST" });
      setResult(outcome);
      onDone?.(outcome.ok);
    } catch (err) {
      setResult({ ok: false, message: err instanceof Error ? err.message : String(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2">
      <Button onClick={run} busy={busy}>
        Test connection
      </Button>
      {result ? <Alert tone={result.ok ? "good" : "bad"}>{result.message}</Alert> : null}
    </div>
  );
}
