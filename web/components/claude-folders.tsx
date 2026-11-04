"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, Details, Field } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { useScope, type Client } from "@/lib/client/scope";
import { slugify } from "@/lib/client/words";

interface Folder {
  folder: string;
  name: string;
  from: string[]; // "Claude Code", "Cowork"
  used_at: number | null; // when Claude last worked in it (seconds)
  holds: number; // other projects' folders inside it
}

interface ClaudeProject {
  id: string;
  name: string;
  description: string;
  instructions: boolean;
  files: number;
  used_at: number | null;
}

/** One thing to offer: a folder (maybe also a Claude project of the same name), or a
 * Claude project on its own. */
interface Offer {
  key: string; // the folder, or "claude:<id>"
  title: string;
  folder?: Folder;
  project?: ClaudeProject;
  usedAt: number;
  client: string; // the likely client's name, or ""
}

// Folders that hold work rather than being one client's: not a client's name.
const GENERIC = new Set(["github", "gitlab", "repos", "repositories", "projects", "code", "src", "dev", "work", "documents", "desktop", "downloads", "onedrive", "dropbox", "users", "home", "sites", "clients", "apps", "workspace", "workspaces"]);

/** A project name not taken yet, from a folder's or a Claude project's name. */
function freeName(text: string, taken: Set<string>): string {
  const base = slugify(text) || "project";
  let name = base;
  for (let n = 2; taken.has(name); n++) name = `${base.slice(0, 60)}-${n}`;
  return name;
}

/** Whose work it likely is: a client whose name is in its path or name, else the
 * folder it sits in (…\Clients\Seaside\site), unless that's a generic one. */
function likelyClient(offer: { title: string; folder?: Folder }, clients: Client[]): string {
  const text = slugify(`${offer.folder?.folder ?? ""} ${offer.title}`);
  const known = clients.find((c) => c.slug.length > 2 && `-${text}-`.includes(`-${c.slug}-`));
  if (known) return known.name;
  const parts = (offer.folder?.folder ?? "").split(/[\\/]/).filter(Boolean);
  const parent = parts[parts.length - 2] ?? "";
  if (!parent || GENERIC.has(parent.toLowerCase()) || parts.length < 3 || /^[a-z]:$/i.test(parent)) return "";
  const grand = (parts[parts.length - 3] ?? "").toLowerCase();
  // Only under a folder of clients, or a name that isn't a person's user folder.
  return grand === "users" ? "" : parent;
}

function when(seconds: number): string {
  if (!seconds) return "";
  const days = Math.floor((Date.now() / 1000 - seconds) / 86400);
  return days < 1 ? "used today" : days === 1 ? "used yesterday" : days < 60 ? `used ${days} days ago` : `used ${new Date(seconds * 1000).toLocaleDateString()}`;
}

/**
 * The desktop app offers what you've worked on with Claude as ready-made
 * projects: the folders you opened in Claude Code or picked in Cowork (each
 * becomes a project that uses the folder as it is), and your Claude projects
 * from Chat and Cowork (each gets a new folder with its instructions and files).
 * Newest first, grouped by whose work it likely is; a Claude project named like a
 * folder shows once (as the folder), and folders that hold other projects are
 * folded away. Tick the client work, say whose it is. Shows nothing when there's
 * nothing.
 */
export function ClaudeCodeFolders({ onAdded, className = "" }: { onAdded: (names: string[]) => void; className?: string }) {
  const folders = useApi<Folder[]>(DESKTOP ? "desktop/claude-code-folders" : null);
  const claudeProjects = useApi<ClaudeProject[]>(DESKTOP ? "desktop/claude-projects" : null);
  const projects = useApi<{ name: string }[]>(DESKTOP ? "projects" : null);
  const scope = useScope();
  // offer key -> client, when ticked
  const [clients, setClients] = useState<Record<string, string>>({});
  const [lastClient, setLastClient] = useState("");
  const [search, setSearch] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const taken = new Set((projects.data ?? []).map((p) => p.name));
  // A Claude project already brought in (a project of the same name) isn't offered again.
  const offeredProjects = (claudeProjects.data ?? []).filter((p) => !taken.has(slugify(p.name)));
  const bySlug = new Map((folders.data ?? []).map((f) => [slugify(f.name), f]));
  const merged = new Set<string>();
  const offers: Offer[] = [];
  for (const folder of folders.data ?? []) {
    const project = offeredProjects.find((p) => slugify(p.name) === slugify(folder.name));
    if (project) merged.add(project.id);
    const usedAt = Math.max(folder.used_at ?? 0, project?.used_at ?? 0);
    offers.push({ key: folder.folder, title: folder.name, folder, project, usedAt, client: "" });
  }
  for (const project of offeredProjects) {
    if (merged.has(project.id) || bySlug.has(slugify(project.name))) continue;
    offers.push({ key: `claude:${project.id}`, title: project.name, project, usedAt: project.used_at ?? 0, client: "" });
  }
  for (const offer of offers) offer.client = likelyClient(offer, scope.clients);
  offers.sort((a, b) => b.usedAt - a.usedAt || a.title.localeCompare(b.title));
  // Linked to as /projects/new#from-claude: it shows once its lists load.
  const count = offers.length;
  useEffect(() => {
    if (count && window.location.hash === "#from-claude") document.getElementById("from-claude")?.scrollIntoView({ block: "start" });
  }, [count]);
  if (!offers.length) return null;

  const words = search.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = (o: Offer) => !words.length || words.every((w) => `${o.title} ${o.folder?.folder ?? ""} ${o.project?.description ?? ""} ${o.client}`.toLowerCase().includes(w));
  const shown = offers.filter(matches);
  const holders = shown.filter((o) => (o.folder?.holds ?? 0) > 0);
  const work = shown.filter((o) => !((o.folder?.holds ?? 0) > 0));
  const groups = [...new Set(work.map((o) => o.client))].sort((a, b) => (a ? (b ? a.localeCompare(b) : -1) : 1));
  const ticked = Object.entries(clients);
  const ready = ticked.length > 0 && ticked.every(([, client]) => client.trim());

  function tick(offer: Offer, on: boolean) {
    setClients(({ [offer.key]: _, ...rest }) => (on ? { ...rest, [offer.key]: offer.client || lastClient } : rest));
  }

  function setClient(key: string, client: string) {
    setClients((all) => ({ ...all, [key]: client }));
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
        const offer = offers.find((o) => o.key === key);
        if (!offer) continue;
        const name = freeName(offer.title, names);
        // A folder (also when it's a Claude project too) is used as it is.
        if (offer.folder) await api("projects", { json: { name, title: offer.title === name ? "" : offer.title, owner, workspace_path: offer.folder.folder, description: offer.project?.description ?? "" } });
        else if (offer.project) await api(`desktop/claude-projects/${encodeURIComponent(offer.project.id)}`, { json: { name, owner } });
        names.add(name);
        added.push(name);
      }
      setClients({});
      folders.reload();
      claudeProjects.reload();
      projects.reload();
      scope.reloadClients(); // a new client is one now
      onAdded(added);
    } catch (err) {
      setError(`${added.length ? `Added ${added.join(", ")}; then: ` : ""}${err instanceof Error ? err.message : String(err)}`);
      folders.reload();
      claudeProjects.reload();
      projects.reload();
    } finally {
      setBusy(false);
    }
  }

  const brings = (p: ClaudeProject) =>
    [p.instructions ? "its instructions (as CLAUDE.md)" : "", p.files ? `${p.files} file${p.files === 1 ? "" : "s"}` : ""].filter(Boolean).join(" and ");

  const row = (offer: Offer) => {
    const on = offer.key in clients;
    const source = [offer.folder?.from.join(", "), offer.project ? (offer.folder ? "also a Claude project" : "Claude project (Chat and Cowork)") : "", when(offer.usedAt)].filter(Boolean).join(" · ");
    return (
      <li key={offer.key} className="rounded-xl border border-line p-3">
        <label className="flex items-start gap-3">
          <input type="checkbox" checked={on} onChange={(e) => tick(offer, e.target.checked)} className="mt-1 size-5 accent-[var(--color-accent)]" />
          <span className="min-w-0">
            <span className="block font-medium">{offer.title}</span>
            {offer.folder ? <span className="block font-mono text-xs break-all text-muted">{offer.folder.folder}</span> : null}
            {offer.project?.description ? <span className="block text-sm text-muted">{offer.project.description}</span> : null}
            <span className="block text-xs text-muted">{source}</span>
            {offer.project && !offer.folder ? <span className="block text-xs text-muted">Gets a new folder with {brings(offer.project) || "its name and description"}; its conversations stay in Claude.</span> : null}
            {offer.folder?.holds ? <span className="block text-xs text-muted">Holds {offer.folder.holds} other project folder{offer.folder.holds === 1 ? "" : "s"}.</span> : null}
          </span>
        </label>
        {on ? (
          <div className="mt-2 pl-8">
            <Field label="Client" value={clients[offer.key] ?? ""} onChange={(v) => setClient(offer.key, v)} placeholder="General Informatics LLC" required />
          </div>
        ) : null}
      </li>
    );
  };

  return (
    <Card className={`space-y-3 ${className}`}>
      <h2 id="from-claude" className="text-lg font-semibold">
        From Claude
      </h2>
      <p className="text-sm text-muted">What you&apos;ve worked on with Claude, most recent first. Tick the ones that are client work and say whose each one is. A folder stays as it is; a Claude project gets a new folder with its instructions and files.</p>
      {offers.length > 6 ? <Field label="Search" value={search} onChange={setSearch} placeholder="a folder, project or client" /> : null}
      {groups.map((group) => (
        <section key={group || "unsure"} className="space-y-2">
          <h3 className="text-sm font-medium">{group ? `Likely ${group}` : groups.length > 1 ? "Not sure whose" : "Your work with Claude"}</h3>
          <ul className="space-y-2">{work.filter((o) => o.client === group).map(row)}</ul>
        </section>
      ))}
      {holders.length ? (
        <Details label={`Folders that hold other projects (${holders.length})`}>
          <p className="mb-2 text-xs text-muted">Usually a folder of projects rather than one. Add it only if it&apos;s one piece of client work.</p>
          <ul className="space-y-2">{holders.map(row)}</ul>
        </Details>
      ) : null}
      {words.length && !shown.length ? <p className="text-sm text-muted">Nothing matches.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!ready} onClick={add}>
        {ticked.length > 1 ? `Add ${ticked.length} projects` : "Add project"}
      </Button>
    </Card>
  );
}
