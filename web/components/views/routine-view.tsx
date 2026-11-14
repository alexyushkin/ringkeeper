"use client";

import Link from "@/components/link";
import { BackLink } from "@/components/history-arrows";
import { useRouter } from "next/navigation";
import { useState } from "react";

import type { AgentData } from "@/components/agents";
import { DecisionCard, type Decision } from "@/components/decision-card";
import { Markdown } from "@/components/markdown";
import { RoutineForm, RunStatus, TRIGGER, type RoutineData, type Run } from "@/components/routines";
import { Alert, Badge, Button, Card, Code, ConfirmButton, Details, Empty, Loading, Section, money, timeAgo } from "@/components/ui";
import { LoadMore } from "@/components/tidy";
import { api, useApi, useLive, usePaged } from "@/lib/client/api";
import { projectHref, taskHref } from "@/lib/client/routes";
import { place } from "@/lib/client/scope";

/** One routine: what it does and when, Test run / Run now / turn on or pause, its
 * webhook address, what its runs are waiting for, and every run. */
export function RoutineView({ id }: { id: string }) {
  const router = useRouter();
  const routine = useApi<RoutineData>(`routines/${id}`);
  const runs = usePaged<Run>(`routines/${id}/runs`);
  const pending = useApi<Decision[]>(routine.data ? "decisions" : null, { status: "pending", routine: routine.data?.id, limit: 500 });
  const agents = useApi<AgentData[]>(routine.data ? `projects/${routine.data.project}/agents` : null);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useLive((event) => {
    if (event.type === "task") runs.reload();
    if (event.type === "approval") pending.reload();
  });

  if (routine.error) return <Alert>{routine.error}</Alert>;
  if (!routine.data) return <Loading />;
  const r = routine.data;
  const waiting = pending.data ?? [];

  async function act(path: string, json: unknown, done: string) {
    setError(null);
    setNotice(null);
    try {
      const result = await api<{ skipped?: string | null }>(`routines/${id}/${path}`, { json });
      setNotice(result?.skipped ? `Skipped: ${result.skipped}.` : done);
      routine.reload();
      runs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <>
      <BackLink href={projectHref(r.project) + "#routines"}>‹ {place(r.client, r.project)}</BackLink>
      <div className="mt-2 mb-1 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">{r.name}</h1>
        {r.paused ? <Badge>paused</Badge> : r.project_paused ? <Badge tone="warn">project paused</Badge> : <Badge tone="good">on</Badge>}
      </div>
      <p className="mb-4 text-sm text-muted">
        {r.when} · runs the {r.agent} agent
        {!r.paused && r.next_run_at ? ` · next ${new Date(r.next_run_at).toLocaleString()}` : ""}
      </p>

      <div className="mb-4 flex flex-wrap gap-2">
        <Button kind={r.paused ? "primary" : "secondary"} onClick={() => act("run", { test: r.paused }, r.paused ? "Test run started: its result shows below." : "Started.")}>
          {r.paused ? "Test run" : "Run now"}
        </Button>
        <Button kind={r.paused ? "secondary" : "ghost"} onClick={() => act("paused", { paused: !r.paused }, r.paused ? "Turned on." : "Paused.")}>
          {r.paused ? "Turn on" : "Pause"}
        </Button>
        <Button kind="ghost" onClick={() => setEditing(!editing)}>
          {editing ? "Close" : "Edit"}
        </Button>
        <ConfirmButton
          kind="secondary"
          question={`Remove ${r.name}? Its run history goes with it; the tasks it ran stay.`}
          confirm={`Remove ${r.name}`}
          onConfirm={async () => {
            await api(`routines/${id}`, { method: "DELETE" });
            router.push(projectHref(r.project) + "#routines");
          }}
        >
          Remove
        </ConfirmButton>
      </div>
      {notice ? <Alert tone="good">{notice}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {r.paused ? <p className="mb-4 text-sm text-muted">Paused: it runs only when you press Test run. Turn it on when the result looks right.</p> : null}

      {editing && agents.data ? (
        <Card className="mb-4">
          <RoutineForm
            project={r.project}
            agents={agents.data}
            existing={r}
            onSaved={() => {
              setEditing(false);
              routine.reload();
            }}
          />
        </Card>
      ) : null}

      {r.hook_path ? (
        <Section id="webhook" title="Webhook" summary="POST to run it">
          <p className="text-sm text-muted">Each POST runs it once, with the body as {"{{event}}"}. Send an Idempotency-Key header to make a repeated call count once.</p>
          <Code>{r.hook_url ?? r.hook_path}</Code>
          <p className="text-xs text-muted">It answers only on this computer. Keep the address private: anyone who has it can start the routine.</p>
        </Section>
      ) : null}

      {waiting.length ? (
        <div className="mb-4 space-y-3">
          <h2 className="text-sm font-semibold text-muted">Waiting for you</h2>
          {waiting.map((d) => (
            <DecisionCard key={d.id} decision={d} onDone={pending.reload} />
          ))}
        </div>
      ) : null}

      <Section id="runs" title="Runs" summary={runs.total !== undefined ? String(runs.total) : undefined}>
        {runs.items && !runs.items.length ? <Empty>No runs yet. Press Test run to try it once.</Empty> : null}
        <ul className="space-y-2">
          {(runs.items ?? []).map((run) => (
            <li key={run.id} className="rounded-xl border border-line p-3 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <RunStatus run={run} />
                <span>{TRIGGER[run.trigger] ?? run.trigger}</span>
                {run.missed ? <span className="text-xs text-muted">made up for {run.missed} missed</span> : null}
                <span className="ml-auto text-xs text-muted">{timeAgo(run.created_at)}</span>
              </div>
              {run.skipped ? <p className="mt-1 text-xs text-muted">Skipped: {run.skipped}.</p> : null}
              {run.summary ? <Markdown text={run.summary} className="mt-1" /> : null}
              {run.error && run.status !== "succeeded" ? <p className="mt-1 text-xs text-bad">{run.error}</p> : null}
              {run.task_id ? (
                <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted">
                  <span>{run.steps ?? 0} steps</span>
                  <span>{money(run.cost_usd)} (estimate)</span>
                  <Link href={taskHref(run.task_id)} className="text-accent">
                    Open the task ›
                  </Link>
                </div>
              ) : null}
            </li>
          ))}
        </ul>
        <LoadMore list={runs} />
      </Section>

      <Details label="Show the prompt">
        <Code>{r.prompt}</Code>
      </Details>
    </>
  );
}
