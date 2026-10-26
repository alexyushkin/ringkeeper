"use client";

import { useState } from "react";

import { Alert, Button, Card, Field } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { slugify } from "@/lib/client/words";

interface Folder {
  folder: string;
  name: string;
}

/**
 * The desktop app offers the folders the user opened Claude Code in as
 * ready-made projects: tick the client work, say whose it is. Each becomes a
 * project that uses the folder as it is. Shows nothing when there are none.
 */
export function ClaudeCodeFolders({ onAdded, className = "" }: { onAdded: (names: string[]) => void; className?: string }) {
  const folders = useApi<Folder[]>(DESKTOP ? "desktop/claude-code-folders" : null);
  const projects = useApi<{ name: string }[]>(DESKTOP ? "projects" : null);
  const [clients, setClients] = useState<Record<string, string>>({}); // folder -> client, when ticked
  const [lastClient, setLastClient] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!folders.data?.length) return null;
  const ticked = Object.entries(clients);
  const ready = ticked.length > 0 && ticked.every(([, client]) => client.trim());

  function tick(folder: string, on: boolean) {
    setClients(({ [folder]: _, ...rest }) => (on ? { ...rest, [folder]: lastClient } : rest));
  }

  function setClient(folder: string, client: string) {
    setClients((all) => ({ ...all, [folder]: client }));
    setLastClient(client);
  }

  async function add() {
    setBusy(true);
    setError(null);
    const taken = new Set((projects.data ?? []).map((p) => p.name));
    const added: string[] = [];
    try {
      for (const [folder, client] of ticked) {
        const base = slugify(folders.data?.find((f) => f.folder === folder)?.name ?? "") || "project";
        let name = base;
        for (let n = 2; taken.has(name); n++) name = `${base.slice(0, 60)}-${n}`;
        await api("projects", { json: { name, owner: slugify(client) || client.trim(), workspace_path: folder } });
        taken.add(name);
        added.push(name);
      }
      setClients({});
      folders.reload();
      onAdded(added);
    } catch (err) {
      setError(`${added.length ? `Added ${added.join(", ")}; then: ` : ""}${err instanceof Error ? err.message : String(err)}`);
      folders.reload();
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className={`space-y-3 ${className}`}>
      <h2 className="text-lg font-semibold">From Claude Code</h2>
      <p className="text-sm text-muted">
        Folders you've opened in Claude Code. Tick the ones that are client work and say whose each one is. Each becomes a project; its files stay as they are.
      </p>
      <ul className="space-y-2">
        {folders.data.map((f) => {
          const on = f.folder in clients;
          return (
            <li key={f.folder} className="rounded-xl border border-line p-3">
              <label className="flex items-start gap-3">
                <input type="checkbox" checked={on} onChange={(e) => tick(f.folder, e.target.checked)} className="mt-1 size-5 accent-[var(--color-accent)]" />
                <span className="min-w-0">
                  <span className="block font-medium">{f.name}</span>
                  <span className="block font-mono text-xs break-all text-muted">{f.folder}</span>
                </span>
              </label>
              {on ? (
                <div className="mt-2 pl-8">
                  <Field label="Client" value={clients[f.folder] ?? ""} onChange={(v) => setClient(f.folder, v)} placeholder="Acme Inc." required />
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!ready} onClick={add}>
        {ticked.length > 1 ? `Add ${ticked.length} projects` : "Add project"}
      </Button>
    </Card>
  );
}
