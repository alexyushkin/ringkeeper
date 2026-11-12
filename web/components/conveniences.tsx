"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import Link from "@/components/link";
import { fillRequest } from "@/components/composer";
import { EnvChoice, nameFor, useTakenNames } from "@/components/connection-form";
import { ConnectionAdded } from "@/components/connection-added";
import { Alert, Badge, Button, Card, Field } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { taskHref } from "@/lib/client/routes";
import { clientName, projectName, sameClient } from "@/lib/client/scope";

/* Small things that save a trip: the digest and shortcuts on Home, Run again and saving a
 * request on a task, and a project's own keys offered as connections. */

const ATTACHED = "Files attached to this request:";

interface ClientDay {
  client: string;
  name: string;
  ran: number;
  failed: string[]; // broke: failed, out of time, over its cap
  not_done: string[]; // ran, and said it couldn't do what was asked
  waiting: string[];
}

interface Day {
  since: string;
  clients: ClientDay[];
  left_out: { label: string; href: string; link: string }[]; // Check my setup's requests, the demo's
}

/** When the window started, as a person says it: "Wed 12:16 PM". */
function sinceWords(since: string): string {
  return new Date(since).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" });
}

/** The tasks a count stands for: the same client, outcome and window, the owner's own work. */
function tasksLink(client: string, since: string, outcome?: "broke" | "not_done"): string {
  const query = new URLSearchParams({ client, since, real: "1", ...(outcome ? { outcome } : {}) });
  return `/tasks?${query}`;
}

/** Home: per client, what ran, wasn't done, failed and waits, for the last day. Check my
 * setup's requests and the demo's aren't counted: a muted line says they ran. */
export function DigestCard({ client }: { client?: string }) {
  const digest = useApi<Day>("digest");
  useLive((event) => {
    if (event.type === "task" || event.type === "approval") digest.reload();
  });
  const days = (digest.data?.clients ?? []).filter((day) => !client || sameClient(day.client, client));
  const leftOut = digest.data?.left_out ?? [];
  if (!days.length && !leftOut.length) return null;
  const since = digest.data?.since ?? "";
  return (
    <Card className="mt-4 space-y-2">
      <h2 className="text-sm font-semibold text-muted" title={since ? `since ${sinceWords(since)}` : undefined}>
        The last 24 hours
      </h2>
      <ul className="space-y-1 text-sm">
        {days.map((day) => (
          <li key={day.client} className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{day.name}</span>
            <Link href={tasksLink(day.client, since)} className="text-muted hover:text-accent">
              {day.ran} ran
            </Link>
            {day.not_done.length ? (
              <Link href={tasksLink(day.client, since, "not_done")} title={day.not_done.join("\n")}>
                <Badge tone="warn">{day.not_done.length} not done</Badge>
              </Link>
            ) : null}
            {day.failed.length ? (
              <Link href={tasksLink(day.client, since, "broke")} title={day.failed.join("\n")}>
                <Badge tone="bad">{day.failed.length} failed</Badge>
              </Link>
            ) : null}
            {day.waiting.length ? (
              <Link href="/approvals">
                <Badge tone="warn">{day.waiting.length} waiting for you</Badge>
              </Link>
            ) : null}
          </li>
        ))}
      </ul>
      {leftOut.map((line) => (
        <p key={line.label} className="text-xs text-muted">
          {line.label} ·{" "}
          <Link href={line.href} className="hover:text-accent">
            {line.link}
          </Link>
        </p>
      ))}
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
            <button type="button" title={`${projectName(shortcut.project)}: ${shortcut.prompt}`} className="px-3 py-1 hover:text-accent" onClick={() => fillRequest(shortcut.prompt.includes(`@${projectName(shortcut.project)}`) ? shortcut.prompt : `@${projectName(shortcut.project)} ${shortcut.prompt}`)}>
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
          <span className="self-center text-sm text-muted">★ Saved on {projectName(project)}: it&apos;s on Home.</span>
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
            Save on {projectName(project)}
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
  warning: string;
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
      {finding.warning ? <Alert tone="warn">{finding.warning}</Alert> : null}
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
      <p className="text-xs text-muted">These look like {clientName(owner)}&apos;s keys and tools. Nothing is added until you connect one; values stay in the file until then.</p>
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

interface FoundLogin {
  id: string;
  tool: string;
  account: string;
  title: string;
  usable: boolean;
  why: string;
  added: boolean;
}

/** Tools you're signed in to on this computer, offered once each as your own connections:
 * you choose which projects use them. Nothing is added until you connect one. */
export function FoundLogins({ onChanged }: { onChanged: () => void }) {
  const logins = useApi<{ found: FoundLogin[] }>("logins");
  const taken = useTakenNames();
  const [added, setAdded] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = (logins.data?.found ?? []).filter((login) => !login.added);

  async function connect(login: FoundLogin) {
    setBusy(login.id);
    setError(null);
    const name = nameFor(login.title, undefined, taken);
    try {
      await api("logins/connect", { json: { id: login.id, name, title: login.title } });
      setAdded(name);
      logins.reload();
      onChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  if (added) return <ConnectionAdded name={added} onLinked={onChanged} onDone={() => setAdded(null)} />;
  if (!open.length) return null;
  return (
    <div className="space-y-2 rounded-xl bg-sunken p-3">
      <h3 className="text-sm font-semibold">Signed in on this computer</h3>
      <p className="text-xs text-muted">Use a login you already have instead of pasting a key. Each becomes your own tool, and you choose which projects use it. Nothing is added until you connect one.</p>
      <ul className="space-y-2">
        {open.map((login) => (
          <li key={login.id} className="space-y-1 rounded-xl border border-line p-3 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{login.tool}</span>
              <span className="text-xs text-muted">{login.account}</span>
              <span className="ml-auto" />
              {login.usable ? (
                <Button busy={busy === login.id} onClick={() => void connect(login)}>
                  Connect
                </Button>
              ) : null}
              <Button kind="ghost" onClick={() => void api("logins/skip", { json: { id: login.id } }).then(() => logins.reload())}>
                Skip
              </Button>
            </div>
            {login.why ? <p className="text-xs text-muted">{login.why}</p> : null}
          </li>
        ))}
      </ul>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
