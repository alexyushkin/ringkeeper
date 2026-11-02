"use client";

import { useState } from "react";

import { Alert, Button, Card, Field } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { slugify } from "@/lib/client/words";

interface Folder {
  folder: string;
  name: string;
  from: string[]; // "Claude Code", "Cowork"
}

interface ClaudeProject {
  id: string;
  name: string;
  description: string;
  instructions: boolean;
  files: number;
}

/** A project name not taken yet, from a folder's or a Claude project's name. */
function freeName(text: string, taken: Set<string>): string {
  const base = slugify(text) || "project";
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base.slice(0, 60)}-${n}`;
  return name;
}

/**
 * The desktop app offers what you've worked on with Claude as ready-made
 * projects: the folders you opened in Claude Code or picked in Cowork (each
 * becomes a project that uses the folder as it is), and your Claude projects
 * from Chat and Cowork (each gets a new folder with its instructions and files).
 * Tick the client work, say whose it is. Shows nothing when there's nothing.
 */
export function ClaudeCodeFolders({ onAdded, className = "" }: { onAdded: (names: string[]) => void; className?: string }) {
  const folders = useApi<Folder[]>(DESKTOP ? "desktop/claude-code-folders" : null);
  const claudeProjects = useApi<ClaudeProject[]>(DESKTOP ? "desktop/claude-projects" : null);
  const projects = useApi<{ name: string }[]>(DESKTOP ? "projects" : null);
  // folder (or "claude:<id>" for a Claude project) -> client, when ticked
  const [clients, setClients] = useState<Record<string, string>>({});
  const [lastClient, setLastClient] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const taken = new Set((projects.data ?? []).map((p) => p.name));
  // A Claude project already brought in (a project of the same name) isn't offered again.
  const offered = (claudeProjects.data ?? []).filter((p) => !taken.has(slugify(p.name)));
  if (!folders.data?.length && !offered.length) return null;
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
    const names = new Set(taken);
    const added: string[] = [];
    try {
      for (const [key, client] of ticked) {
        const owner = client.trim(); // a new client is created as typed
        const claude = key.startsWith("claude:") ? offered.find((p) => `claude:${p.id}` === key) : undefined;
        const name = freeName(claude ? claude.name : (folders.data?.find((f) => f.folder === key)?.name ?? ""), names);
        if (claude) await api(`desktop/claude-projects/${encodeURIComponent(claude.id)}`, { json: { name, owner } });
        else await api("projects", { json: { name, owner, workspace_path: key } });
        names.add(name);
        added.push(name);
      }
      setClients({});
      folders.reload();
      projects.reload();
      onAdded(added);
    } catch (err) {
      setError(`${added.length ? `Added ${added.join(", ")}; then: ` : ""}${err instanceof Error ? err.message : String(err)}`);
      folders.reload();
      projects.reload();
    } finally {
      setBusy(false);
    }
  }

  const row = (key: string, title: string, detail: React.ReactNode) => {
    const on = key in clients;
    return (
      <li key={key} className="rounded-xl border border-line p-3">
        <label className="flex items-start gap-3">
          <input type="checkbox" checked={on} onChange={(e) => tick(key, e.target.checked)} className="mt-1 size-5 accent-[var(--color-accent)]" />
          <span className="min-w-0">
            <span className="block font-medium">{title}</span>
            {detail}
          </span>
        </label>
        {on ? (
          <div className="mt-2 pl-8">
            <Field label="Client" value={clients[key] ?? ""} onChange={(v) => setClient(key, v)} placeholder="General Informatics LLC" required />
          </div>
        ) : null}
      </li>
    );
  };
  const brings = (p: ClaudeProject) =>
    [p.instructions ? "its instructions (as CLAUDE.md)" : "", p.files ? `${p.files} file${p.files === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");

  return (
    <Card className={`space-y-3 ${className}`}>
      <h2 className="text-lg font-semibold">From Claude</h2>
      <p className="text-sm text-muted">What you&apos;ve worked on with Claude. Tick the ones that are client work and say whose each one is. Each becomes a project.</p>
      {folders.data?.length ? (
        <>
          <h3 className="text-sm font-medium">Folders from Claude Code and Cowork</h3>
          <p className="text-xs text-muted">The project uses the folder; its files stay as they are.</p>
          <ul className="space-y-2">
            {folders.data.map((f) =>
              row(
                f.folder,
                f.name,
                <>
                  <span className="block font-mono text-xs break-all text-muted">{f.folder}</span>
                  <span className="block text-xs text-muted">{f.from.join(", ")}</span>
                </>,
              ),
            )}
          </ul>
        </>
      ) : null}
      {offered.length ? (
        <>
          <h3 className="text-sm font-medium">Claude projects (Chat and Cowork)</h3>
          <p className="text-xs text-muted">Each gets a new folder with its instructions and files; its conversations stay in Claude. These are the projects the Claude app has used in Cowork.</p>
          <ul className="space-y-2">
            {offered.map((p) =>
              row(
                `claude:${p.id}`,
                p.name,
                <>
                  {p.description ? <span className="block text-sm text-muted">{p.description}</span> : null}
                  <span className="block text-xs text-muted">{brings(p) ? `Brings ${brings(p)}.` : "Just its name and description."}</span>
                </>,
              ),
            )}
          </ul>
        </>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!ready} onClick={add}>
        {ticked.length > 1 ? `Add ${ticked.length} projects` : "Add project"}
      </Button>
    </Card>
  );
}
