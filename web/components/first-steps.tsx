"use client";

import { useState } from "react";

import Link from "@/components/link";
import { fillRequest } from "@/components/composer";
import { Alert, Button, Card, ConfirmButton } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { projectName } from "@/lib/client/scope";

export interface DemoInfo {
  client: string;
  project: string;
  connection: string;
  folder: string;
  try: string[];
}

interface Start {
  checklist: { claude: boolean; project: boolean; connection: boolean; task: boolean; done: boolean; dismissed: boolean; unlinked: Unlinked[] };
  suggestions: string[];
  demo: DemoInfo | null;
  demo_offered: boolean;
  whats_new: { version: string; notes: string } | null;
  backup_nudge?: boolean; // desktop: real tasks ran, automatic backups are off, not dismissed
  backup_upload_error?: string | null; // the last upload to Google Drive didn't work
}

/** Home's first steps: the checklist (until done or dismissed), the demo, and
 * requests to try, built from your own projects and connections. */
export function FirstSteps() {
  const start = useApi<Start>("start");
  useLive((event) => {
    if (["task", "message_routed"].includes(event.type)) start.reload();
  });
  if (!start.data) return null;
  const { checklist, suggestions, demo, demo_offered, whats_new, backup_nudge, backup_upload_error } = start.data;
  const showList = !checklist.done && !checklist.dismissed;
  const tries = demo ? suggestions.filter((s) => !demo.try.includes(s)) : suggestions;
  return (
    <div className="mb-6 space-y-3">
      {whats_new ? <WhatsNew {...whats_new} onSeen={start.reload} /> : null}
      {showList ? <Checklist steps={checklist} demoOffered={demo_offered && !demo} onChange={start.reload} /> : null}
      {!showList && checklist.unlinked.length ? <UnlinkedNote names={checklist.unlinked} /> : null}
      {backup_nudge ? <BackupNudge onDismissed={start.reload} /> : null}
      {backup_upload_error ? (
        <Alert tone="warn">
          {backup_upload_error} The copy on this computer is saved.{" "}
          <Link href="/settings#backup/backup-drive" className="font-medium">
            Backup settings
          </Link>
        </Alert>
      ) : null}
      {demo ? <DemoCard demo={demo} onRemoved={start.reload} /> : null}
      {tries.length ? <Suggestions items={tries} /> : null}
    </div>
  );
}

/** After an update: what came with it (its CHANGELOG section), once. */
function WhatsNew({ version, notes, onSeen }: { version: string; notes: string; onSeen: () => void }) {
  const items = notes
    .split(/\n(?=- )/)
    .map((item) => item.replace(/^- /, "").replace(/\s*\n\s*/g, " ").trim())
    .filter(Boolean);
  return (
    <Card className="space-y-3 border-accent">
      <div className="flex items-start gap-2">
        <h2 className="flex-1 font-semibold">What&apos;s new in {version}</h2>
        <Button kind="ghost" onClick={() => void api("start/whats-new", { method: "POST" }).then(onSeen)}>
          Got it
        </Button>
      </div>
      <ul className="max-h-72 list-disc space-y-2 overflow-y-auto pl-5 text-sm">
        {items.map((item) => {
          const bold = item.match(/^\*\*(.+?)\*\*\s*(.*)$/s);
          return (
            <li key={item.slice(0, 40)}>
              {bold ? (
                <>
                  <strong>{bold[1]}</strong> {bold[2]?.replace(/`([^`]+)`/g, "$1")}
                </>
              ) : (
                item.replace(/\*\*/g, "")
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

/** Once there's real work and automatic backups are off: asked once, until Not now or
 * they're turned on. */
function BackupNudge({ onDismissed }: { onDismissed: () => void }) {
  return (
    <Card className="space-y-2">
      <h2 className="font-semibold">Turn on automatic backups</h2>
      <p className="text-sm text-muted">
        Your projects, connections and history are only on this computer. A weekly backup into a folder you choose (another drive, or a synced folder) brings them back if this computer is lost or replaced.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <Link href="/settings#backup/backup-auto/run" className="text-sm font-medium text-accent">
          Turn them on
        </Link>
        <Button kind="ghost" onClick={() => void api("start/backup-nudge", { method: "POST" }).then(onDismissed)}>
          Not now
        </Button>
      </div>
    </Card>
  );
}

function Step({ done, children, action }: { done: boolean; children: string; action: React.ReactNode }) {
  return (
    <li className="flex min-h-11 items-center gap-3">
      <span aria-hidden className={`grid size-6 shrink-0 place-items-center rounded-full text-xs font-bold ${done ? "bg-good text-surface" : "border border-line text-muted"}`}>
        {done ? "✓" : ""}
      </span>
      <span className={`flex-1 text-sm ${done ? "text-muted line-through" : ""}`}>
        {children}
        <span className="sr-only">{done ? " (done)" : " (to do)"}</span>
      </span>
      {done ? null : action}
    </li>
  );
}

function StepLink({ href, children }: { href: string; children: string }) {
  return (
    <Link href={href} className="text-sm font-medium text-accent">
      {children}
    </Link>
  );
}

function Checklist({ steps, demoOffered, onChange }: { steps: Start["checklist"]; demoOffered: boolean; onChange: () => void }) {
  const linked = steps.unlinked.length === 0;
  const left = [steps.claude, steps.project, steps.connection, steps.task, linked].filter((s) => !s).length;
  return (
    <Card className="space-y-3">
      <div className="flex items-start gap-2">
        <div className="flex-1">
          <h2 className="font-semibold">Get started</h2>
          <p className="text-sm text-muted">{left === 1 ? "One step left." : `${left} steps left.`} This card goes away when they&apos;re done.</p>
        </div>
        <Button kind="ghost" onClick={() => void api("start/dismiss", { method: "POST" }).then(onChange)} label="Hide the Get started card">
          Hide
        </Button>
      </div>
      <ol className="space-y-1">
        <Step done={steps.claude} action={<StepLink href="/settings#claude">Connect Claude</StepLink>}>
          Connect Claude, the AI that does the work
        </Step>
        <Step done={steps.project} action={<StepLink href="/projects/new">Add a project</StepLink>}>
          Add your first project (one piece of client work)
        </Step>
        <Step done={steps.connection} action={<StepLink href="/connections/new">Add a connection</StepLink>}>
          Connect a service it works with (a database, Airtable, GitHub…)
        </Step>
        {steps.connection ? (
          <Step done={linked} action={linked ? null : <StepLink href={linkHref(steps.unlinked)}>Link</StepLink>}>
            {linked ? "Link every connection to a project" : unlinkedText(steps.unlinked)}
          </Step>
        ) : null}
        <Step
          done={steps.task}
          action={
            <Button kind="ghost" onClick={() => fillRequest("")}>
              Ask for something
            </Button>
          }
        >
          Ask for your first task, in plain words
        </Step>
      </ol>
      {demoOffered ? <TryDemo onAdded={onChange} /> : null}
    </Card>
  );
}

/** A connection linked to no project, and whose it is ("you": your own tool). */
interface Unlinked {
  name: string;
  client: string;
}

/** Where Link goes: the first one's Link to project…, where its card is. */
function linkHref(unlinked: Unlinked[]): string {
  const first = unlinked[0];
  if (!first) return "/connections";
  const at = first.client === "you" ? "/connections" : clientHref(first.client);
  return `${at}#connections/${encodeURIComponent(first.name)}/link`;
}

function unlinkedText(unlinked: Unlinked[]): string {
  const n = unlinked.length;
  return `${n === 1 ? "1 connection isn't" : `${n} connections aren't`} linked to any project, so no task can use ${n === 1 ? "it" : "them"}`;
}

/** Home, when Get started is done or hidden: connections no task can use yet. */
function UnlinkedNote({ names }: { names: Unlinked[] }) {
  return (
    <Card className="flex flex-wrap items-center gap-3">
      <span className="flex-1 text-sm">{unlinkedText(names)}.</span>
      <StepLink href={linkHref(names)}>Link</StepLink>
    </Card>
  );
}

/** "Try it with demo data": first run and Home. */
export function TryDemo({ onAdded }: { onAdded: (demo: DemoInfo) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function add() {
    setBusy(true);
    setError(null);
    try {
      onAdded(await api<DemoInfo>("demo", { method: "POST" }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 rounded-xl bg-sunken p-3">
      <p className="text-sm">
        <strong>Just looking?</strong> Try it with demo data: a made-up client, Demo Co, with a shop&apos;s sample files and database. Run tasks on it and see a change wait for your approval, before connecting anything real. One click removes it all.
      </p>
      <Button busy={busy} onClick={add}>
        Try it with demo data
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

function DemoCard({ demo, onRemoved }: { demo: DemoInfo; onRemoved: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function remove() {
    setError(null);
    try {
      await api("demo", { method: "DELETE" });
      onRemoved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Card className="space-y-3">
      <div>
        <h2 className="font-semibold">Demo data is on</h2>
        <p className="text-sm text-muted">
          {demo.client} is made up: the {projectName(demo.project)} project has sample files and a shop database. It&apos;s set up as production, so a change to it waits for your approval. Try one:
        </p>
      </div>
      <Chips items={demo.try} />
      <ConfirmButton kind="secondary" question={`Remove ${demo.client}: its project, files, database, tasks and conversations?`} confirm="Remove the demo" onConfirm={remove}>
        Remove the demo
      </ConfirmButton>
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

function Suggestions({ items }: { items: string[] }) {
  return (
    <div className="space-y-2">
      <h2 className="text-sm font-semibold text-muted">Try asking</h2>
      <Chips items={items} />
      <p className="text-xs text-muted">These only read: nothing is changed. Pick one, check it, then Send.</p>
    </div>
  );
}

function Chips({ items }: { items: string[] }) {
  return (
    <div className="flex flex-wrap gap-2">
      {items.map((item) => (
        <button key={item} type="button" onClick={() => fillRequest(item)} className="min-h-11 rounded-full border border-line bg-surface px-3 text-left text-sm hover:border-accent">
          {item}
        </button>
      ))}
    </div>
  );
}
