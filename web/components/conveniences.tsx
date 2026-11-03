"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { fillRequest } from "@/components/composer";
import { EnvChoice, nameFor, useTakenNames } from "@/components/connection-form";
import { ConnectionAdded } from "@/components/connection-added";
import { Alert, Badge, Button, Card, Field } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { taskHref } from "@/lib/client/routes";
import { clientName } from "@/lib/client/scope";

/* Small things that save a trip: the digest and shortcuts on Home, Run again and saving a
 * request on a task, and a project's own keys offered as connections. */

const ATTACHED = "Files attached to this request:";

interface ClientDay {
  client: string;
  name: string;
  ran: number;
  failed: string[];
  waiting: string[];
}

/** Home: per client, what ran, failed and waits, for the last day. */
export function DigestCard() {
  const digest = useApi<{ clients: ClientDay[] }>("digest");
  useLive((event) => {
    if (event.type === "task" || event.type === "approval") digest.reload();
  });
  const days = digest.data?.clients ?? [];
  if (!days.length) return null;
  return (
    <Card className="mt-4 space-y-2">
      <h2 className="text-sm font-semibold text-muted">The last 24 hours</h2>
      <ul className="space-y-1 text-sm">
        {days.map((day) => (
          <li key={day.client} className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{day.name}</span>
            <span className="text-muted">{day.ran} ran</span>
            {day.failed.length ? <Badge tone="bad">{day.failed.length} failed</Badge> : null}
            {day.waiting.length ? <Badge tone="warn">{day.waiting.length} waiting for you</Badge> : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

interface Shortcut {
  id: string;
  project: string;
  client: string;
  label: string;
  prompt: string;
}

/** Home: saved requests, a click from the box. */
export function Shortcuts({ project, client }: { project?: string; client?: string }) {
  const shortcuts = useApi<Shortcut[]>("shortcuts");
  const list = (shortcuts.data ?? []).filter((s) => (!project || s.project === project) && (!client || s.client === client));
  if (!list.length) return null;
  return (
    <div className="mt-3">
      <h2 className="mb-1 px-1 text-xs font-semibold text-muted">Saved requests</h2>
      <ul className="flex flex-wrap gap-2">
        {list.map((shortcut) => (
          <li key={shortcut.id} className="flex items-center rounded-full border border-line bg-surface text-sm">
            <button type="button" title={`${shortcut.project}: ${shortcut.prompt}`} className="px-3 py-1 hover:text-accent" onClick={() => fillRequest(shortcut.prompt.includes(`@${shortcut.project}`) ? shortcut.prompt : `@${shortcut.project} ${shortcut.prompt}`)}>
              {shortcut.label}
            </button>
            <button type="button" aria-label={`Remove ${shortcut.label}`} className="pr-2 text-muted hover:text-ink" onClick={() => void api(`shortcuts/${shortcut.id}`, { method: "DELETE" }).then(shortcuts.reload)}>
              ×
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A task's page: the same request again, or saved on its project. */
export function TaskAgain({ id, project, prompt, outcome }: { id: string; project: string; prompt: string; outcome: string | null }) {
  const router = useRouter();
  const [saving, setSaving] = useState(false);
  const [label, setLabel] = useState((outcome ?? prompt).slice(0, 40));
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function again() {
    setError(null);
    try {
      const made = await api<{ id: string }>(`tasks/${id}/again`, { method: "POST" });
      router.push(taskHref(made.id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function save() {
    setError(null);
    try {
      // The request itself: a task's prompt also says which files came with it.
      await api("shortcuts", { json: { project, label: label.trim(), prompt: prompt.split(ATTACHED)[0]?.trim() } });
      setSaving(false);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button onClick={again}>Run again</Button>
        {saved ? (
          <span className="self-center text-sm text-muted">★ Saved on {project}: it&apos;s on Home.</span>
        ) : (
          <Button kind="ghost" onClick={() => setSaving(!saving)}>
            ☆ Save this request
          </Button>
        )}
      </div>
      {saving ? (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-60 flex-1">
            <Field label="Call it" value={label} onChange={setLabel} />
          </div>
          <Button kind="primary" disabled={!label.trim()} onClick={save}>
            Save on {project}
          </Button>
        </div>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

interface Finding {
  id: string;
  file: string;
  service: string;
  label: string;
  keys: string[];
  masked: string;
  title: string;
  usable: boolean;
  why: string;
  added: boolean;
}

/** One key found in the project's files: connected only when you say, one at a time. */
function FoundKey({ project, owner, finding, onDone }: { project: string; owner: string; finding: Finding; onDone: (name?: string) => void }) {
  const [open, setOpen] = useState(false);
  const [env, setEnv] = useState("");
  const [title, setTitle] = useState(finding.title);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const taken = useTakenNames();

  async function connect() {
    setBusy(true);
    setError(null);
    const name = nameFor(title, owner, taken);
    try {
      await api(`projects/${project}/scan/connect`, { json: { id: finding.id, env, name, title: title.trim() } });
      onDone(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 rounded-xl border border-line p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{finding.label}</span>
        <code className="text-xs text-muted">{finding.masked}</code>
        <span className="text-xs text-muted">
          {finding.keys.join(", ")} in {finding.file}
        </span>
        <span className="ml-auto" />
        {finding.added ? (
          <Badge tone="good">added</Badge>
        ) : finding.usable ? (
          <Button onClick={() => setOpen(!open)}>{open ? "Cancel" : "Connect…"}</Button>
        ) : null}
        <Button kind="ghost" onClick={() => void api(`projects/${project}/scan/skip`, { json: { id: finding.id } }).then(() => onDone())}>
          Skip
        </Button>
      </div>
      {finding.why ? <p className="text-xs text-muted">{finding.why}</p> : null}
      {open ? (
        <div className="space-y-3">
          <EnvChoice value={env} onChange={setEnv} />
          <Field label="Name" value={title} onChange={setTitle} />
          <Button kind="primary" busy={busy} disabled={!env || !title.trim()} onClick={connect}>
            Connect for {clientName(owner)}
          </Button>
          {error ? <Alert>{error}</Alert> : null}
        </div>
      ) : null}
    </li>
  );
}

/** Keys in the project's own .env and config files, offered as connections. Read from
 * its folder only; what's shown is masked, and nothing is added until you confirm it. */
export function ProjectScan({ project, owner, onChanged }: { project: string; owner: string; onChanged: () => void }) {
  const scan = useApi<{ folder: string | null; found: Finding[] }>(`projects/${project}/scan`);
  const [added, setAdded] = useState<string | null>(null);
  useEffect(() => setAdded(null), [project]);
  const found = scan.data?.found ?? [];
  const open = found.filter((f) => !f.added);
  if (added) return <ConnectionAdded name={added} onDone={() => { setAdded(null); scan.reload(); onChanged(); }} />;
  if (!open.length) return null;
  return (
    <div className="space-y-2 rounded-xl bg-sunken p-3">
      <h3 className="text-sm font-semibold">Found in this project&apos;s files</h3>
      <p className="text-xs text-muted">These look like {clientName(owner)}&apos;s keys. Nothing is added until you connect one; values stay in the file until then.</p>
      <ul className="space-y-2">
        {open.map((finding) => (
          <FoundKey
            key={finding.id}
            project={project}
            owner={owner}
            finding={finding}
            onDone={(name) => {
              scan.reload();
              if (name) {
                setAdded(name);
                onChanged();
              }
            }}
          />
        ))}
      </ul>
    </div>
  );
}
