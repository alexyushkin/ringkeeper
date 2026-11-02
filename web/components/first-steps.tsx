"use client";

import { useState } from "react";

import Link from "@/components/link";
import { fillRequest } from "@/components/composer";
import { Alert, Button, Card, ConfirmButton } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";

export interface DemoInfo {
  client: string;
  project: string;
  connection: string;
  folder: string;
  try: string[];
}

interface Start {
  checklist: { claude: boolean; project: boolean; connection: boolean; task: boolean; done: boolean; dismissed: boolean };
  suggestions: string[];
  demo: DemoInfo | null;
  demo_offered: boolean;
}

/** Home's first steps: the checklist (until done or dismissed), the demo, and
 * requests to try, built from your own projects and connections. */
export function FirstSteps() {
  const start = useApi<Start>("start");
  useLive((event) => {
    if (["task", "message_routed"].includes(event.type)) start.reload();
  });
  if (!start.data) return null;
  const { checklist, suggestions, demo, demo_offered } = start.data;
  const showList = !checklist.done && !checklist.dismissed;
  const tries = demo ? suggestions.filter((s) => !demo.try.includes(s)) : suggestions;
  return (
    <div className="mb-6 space-y-3">
      {showList ? <Checklist steps={checklist} demoOffered={demo_offered && !demo} onChange={start.reload} /> : null}
      {demo ? <DemoCard demo={demo} onRemoved={start.reload} /> : null}
      {tries.length ? <Suggestions items={tries} /> : null}
    </div>
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
  const left = [steps.claude, steps.project, steps.connection, steps.task].filter((s) => !s).length;
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
          {demo.client} is made up: the {demo.project} project has sample files and a shop database ({demo.connection}). It&apos;s set up as production, so a change to it waits for your approval. Try one:
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
