"use client";

import { useEffect, useState } from "react";

import { useConnectionTitles } from "@/components/connection-form";
import { DecisionCard, type Decision } from "@/components/decision-card";
import { Alert, Button, Empty, Loading, PageHeader, Select, timeUntil } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { ScopeNote } from "@/components/scope-switcher";
import { place, projectName, useScope } from "@/lib/client/scope";

/** Every action waiting for your OK, across conversations, in full: the SQL, the plan,
 * more money for a task. Questions ("which project?", a worker asking something) are
 * asked in their conversation instead. Telegram only gets summaries. J and K move
 * between them, A approves, D denies. */
const KINDS: [string, string][] = [
  ["", "Everything"],
  ["approve", "Live changes"],
  ["plan", "Plans"],
  ["question", "Spending caps"],
];

interface Trust {
  id: string;
  project: string;
  instance: string;
  tool: string;
  expires_at: string;
}

/** The same held action in the same task: the same tool, connection and arguments. */
const sameAction = (d: Decision) => `${d.task_id}|${d.instance}|${d.tool}|${JSON.stringify(d.args)}`;

function typing(target: EventTarget | null): boolean {
  return target instanceof HTMLElement && (["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName) || target.isContentEditable);
}

export default function ApprovalsPage() {
  const scope = useScope();
  const title = useConnectionTitles();
  const [kind, setKind] = useState("");
  const [at, setAt] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const decisions = useApi<Decision[]>("decisions", { ...scope.query, kind, status: "pending", category: "approval", limit: 200 });
  const trusts = useApi<Trust[]>("trusts");
  useLive((event) => {
    if (event.type === "approval") {
      decisions.reload();
      trusts.reload();
    }
  });
  // Production holds first; otherwise newest first, as the API lists them.
  const ordered = [...(decisions.data ?? [])].sort((a, b) => Number(b.production) - Number(a.production));
  const count = ordered.length;
  const current = ordered[Math.min(at, Math.max(count - 1, 0))];
  const currentId = current?.id;

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (typing(event.target) || event.metaKey || event.ctrlKey || event.altKey) return;
      const key = event.key.toLowerCase();
      if (key === "j" || key === "k") {
        event.preventDefault();
        setAt((n) => Math.max(0, Math.min(count - 1, n + (key === "j" ? 1 : -1))));
      } else if ((key === "a" || key === "d") && currentId) {
        const card = document.querySelector(`[data-decision="${currentId}"]`);
        const button = card?.querySelector<HTMLButtonElement>(`[data-${key === "a" ? "approve" : "deny"}] button`);
        if (button?.disabled) card?.querySelector("input")?.focus(); // its name has to be typed
        else button?.click();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [count, currentId]);

  useEffect(() => {
    if (currentId) document.querySelector(`[data-decision="${currentId}"]`)?.scrollIntoView({ block: "nearest" });
  }, [currentId]);

  async function approveAll(group: Decision[]) {
    setError(null);
    try {
      for (const decision of group) {
        await api(`decisions/${decision.id}/answer`, { json: { choice: "approve", text: null, confirm: null, by: "owner" } });
      }
      decisions.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      decisions.reload();
    }
  }

  const groups = new Map<string, Decision[]>();
  for (const decision of ordered) {
    if (decision.kind === "approve" && decision.task_id) groups.set(sameAction(decision), [...(groups.get(sameAction(decision)) ?? []), decision]);
  }

  return (
    <>
      <PageHeader title="Approvals" subtitle="Actions waiting for your OK before Ringkeeper goes on, from every conversation." />
      <ScopeNote />
      <div className="mb-4 grid grid-cols-2 gap-2">
        <Select label="Show" value={kind} onChange={setKind} options={KINDS} />
      </div>
      {trusts.data?.length ? (
        <ul className="mb-4 space-y-1 rounded-xl bg-sunken p-3 text-sm" aria-label="Trusted for now">
          {trusts.data.map((trust) => (
            <li key={trust.id} className="flex flex-wrap items-center gap-2">
              <span>
                Trusted: <strong>{trust.tool.replace(/_/g, " ")}</strong> on {title(trust.instance)} in {projectName(trust.project)}, {timeUntil(trust.expires_at)}
              </span>
              <span className="ml-auto" />
              <Button kind="ghost" onClick={() => void api(`trusts/${trust.id}`, { method: "DELETE" }).then(trusts.reload)}>
                Stop trusting
              </Button>
            </li>
          ))}
        </ul>
      ) : null}
      {decisions.error ? <Alert>{decisions.error}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {decisions.loading && !decisions.data ? <Loading /> : null}
      {decisions.data?.length === 0 ? <Empty>{scope.client || scope.project ? `Nothing is waiting for you from ${place(scope.client, scope.project)}.` : "Nothing is waiting for you. When an agent wants to change live client data, or send something to real people, it asks here first."}</Empty> : null}
      {count > 1 ? <p className="mb-2 hidden text-xs text-muted md:block">Keys: J next · K previous · A approve · D deny</p> : null}
      <div className="space-y-4">
        {ordered.map((decision) => {
          const group = groups.get(sameAction(decision)) ?? [];
          const first = group[0]?.id === decision.id;
          const typed = typeof decision.payload?.destructive === "string";
          return (
            <div key={decision.id} data-decision={decision.id} className={`space-y-2 rounded-2xl ${decision.id === currentId && count > 1 ? "ring-2 ring-accent" : ""}`}>
              {first && group.length > 1 && !typed ? (
                <Button onClick={() => void approveAll(group)}>Approve all {group.length} like this</Button>
              ) : null}
              <DecisionCard decision={decision} onDone={decisions.reload} />
            </div>
          );
        })}
      </div>
    </>
  );
}
