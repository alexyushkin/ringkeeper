"use client";

import { useState } from "react";

import { Alert, Button, LinkButton } from "@/components/ui";
import { api } from "@/lib/client/api";

/** A request or a task held back because its project has no connection of the kind it needs. */
export interface Missing {
  message?: string;
  project: string;
  title: string;
  need: string;
  says?: string;
  label: string;
  href: string;
}

/** What `run` names: a request held before it started ("<message id>"), or a task that ended
 * Not done ("task:<id>"). Runs it, now that the project has the connection. */
export async function runWaiting(run: string): Promise<void> {
  if (run.startsWith("task:")) await api(`tasks/${run.slice(5)}/run-again`, { method: "POST" });
  else await api(`messages/${run}/run-now`, { method: "POST" });
}

function withBack(href: string, back: string): string {
  return `${href}${href.includes("?") ? "&" : "?"}back=${encodeURIComponent(back)}`;
}

/** "Add a Meta Ads connection to Website", opening the add form for that client (linked to the
 * project), and "Run it now" for once it's added. */
export function AddConnection({ missing, back, run, onRan }: { missing: Missing; back: string; run: string; onRan: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await runWaiting(run);
      onRan();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-2 space-y-2">
      <div className="flex flex-wrap gap-2">
        <LinkButton kind="primary" href={withBack(missing.href, back)}>
          {missing.label}
        </LinkButton>
        <Button kind="ghost" busy={busy} onClick={() => void go()}>
          Run it now
        </Button>
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Back from the add form with the connection there: run what was waiting for it. */
export function RunWaiting({ run, back, onBack }: { run: string; back: string; onBack: (to: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await runWaiting(run);
      onBack(back);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-sm">It&apos;s linked to the project. Run the request that was waiting for it?</p>
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} onClick={() => void go()}>
          Run the request now
        </Button>
        {back ? (
          <Button kind="ghost" onClick={() => onBack(back)}>
            Not now
          </Button>
        ) : null}
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
