"use client";

import Link from "@/components/link";
import { useState } from "react";

import { Alert, Badge, Button, Card, Code, Details, Empty, Field, Loading, PageHeader, Select, TextArea, timeAgo } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { place, useScope } from "@/lib/client/scope";
import { KIND } from "@/lib/client/words";
import { taskHref } from "@/lib/client/routes";

interface Decision {
  id: string;
  kind: string;
  status: string;
  summary: string;
  heading: string | null;
  options: string[];
  project: string | null;
  client: string | null;
  production: boolean;
  instance: string | null;
  tool: string | null;
  args: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
  task_id: string | null;
  thread_id: string | null;
  created_at: string;
  expires_at: string;
}

/** Everything waiting for you, in full: the SQL, the plan, the question. Telegram only gets summaries. */
const KINDS: [string, string][] = [
  ["", "Everything"],
  ["approve", "Held calls"],
  ["plan", "Plans"],
  ["question", "Questions"],
  ["route", "Which project"],
];

export default function ApprovalsPage() {
  const scope = useScope();
  const [kind, setKind] = useState("");
  const decisions = useApi<Decision[]>("decisions", { ...scope.query, kind, status: "pending", limit: 200 });
  useLive((event) => {
    if (event.type === "approval") decisions.reload();
  });
  // Production holds first; otherwise newest first, as the API lists them.
  const ordered = [...(decisions.data ?? [])].sort((a, b) => Number(b.production) - Number(a.production));

  return (
    <>
      <PageHeader title="Approvals" subtitle="Things that wait for you before Ringkeeper goes on." />
      <div className="mb-4 grid grid-cols-2 gap-2">
        <Select label="Show" value={kind} onChange={setKind} options={KINDS} />
      </div>
      {decisions.error ? <Alert>{decisions.error}</Alert> : null}
      {decisions.loading && !decisions.data ? <Loading /> : null}
      {decisions.data?.length === 0 ? <Empty>Nothing is waiting for you.</Empty> : null}
      <div className="space-y-4">
        {ordered.map((decision) => (
          <DecisionCard key={decision.id} decision={decision} onDone={decisions.reload} />
        ))}
      </div>
    </>
  );
}

function DecisionCard({ decision, onDone }: { decision: Decision; onDone: () => void }) {
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const payload = decision.payload ?? {};
  const destructive = typeof payload.destructive === "string" ? payload.destructive : null;

  async function answer(choice: string | null, answerText?: string) {
    setBusy(choice ?? "answer");
    setError(null);
    try {
      await api(`decisions/${decision.id}/answer`, {
        json: { choice, text: answerText || null, confirm: confirm || null, by: "owner" },
      });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card className={destructive ? "border-bad" : ""}>
      <div id={decision.id} className="flex flex-wrap items-center gap-2">
        <Badge tone={decision.kind === "approve" ? "bad" : decision.kind === "question" ? "accent" : "warn"}>{KIND[decision.kind] ?? decision.kind}</Badge>
        {decision.project ? <span className="font-medium">{place(decision.client, decision.project)}</span> : null}
        {decision.production ? <Badge tone="bad">production</Badge> : null}
        {decision.instance ? <span className="text-sm text-muted">on {decision.instance}</span> : null}
        <span className="ml-auto text-xs text-muted">{timeAgo(decision.created_at)}</span>
      </div>

      {decision.kind === "approve" ? (
        <div className="mt-3 space-y-3">
          <p className="text-sm">
            <strong>{decision.heading ?? "A change"}</strong> is waiting for your OK. Nothing has run yet.
          </p>
          {typeof decision.args?.sql === "string" ? <Code>{decision.args.sql}</Code> : null}
          <Details>
            <p className="mb-1 text-xs text-muted">Tool: {decision.tool}</p>
            <Code>{JSON.stringify(decision.args, null, 2)}</Code>
          </Details>
          {destructive ? (
            <div className="space-y-2">
              <Alert>This change {destructive}. That can't be undone.</Alert>
              <Field label={`Type ${decision.instance} to approve`} value={confirm} onChange={setConfirm} autoComplete="off" />
            </div>
          ) : null}
          <div className="flex gap-2">
            <Button kind={destructive ? "danger" : "primary"} busy={busy === "approve"} disabled={Boolean(destructive) && confirm.trim() !== decision.instance} onClick={() => answer("approve")}>
              Approve
            </Button>
            <Button busy={busy === "deny"} onClick={() => answer("deny")}>
              Deny
            </Button>
          </div>
        </div>
      ) : null}

      {decision.kind === "plan" ? (
        <div className="mt-3 space-y-3">
          <h3 className="font-medium">{String(payload.title ?? "A plan")}</h3>
          <Code>{String(payload.plan ?? "")}</Code>
          <Details label="Show the worker's instructions">
            <Code>{String(payload.worker_prompt ?? "")}</Code>
          </Details>
          <TextArea label="Note (optional)" value={text} onChange={setText} rows={2} hint="Sent with your answer, e.g. what to change." />
          <div className="flex gap-2">
            <Button kind="primary" busy={busy === "approve"} onClick={() => answer("approve", text)}>
              Approve plan
            </Button>
            <Button busy={busy === "deny"} onClick={() => answer("deny", text)}>
              Ask for changes
            </Button>
          </div>
        </div>
      ) : null}

      {decision.kind === "question" ? (
        <div className="mt-3 space-y-3">
          <p className="whitespace-pre-wrap">{String(payload.question ?? decision.summary)}</p>
          {typeof payload.budget === "number" ? (
            <div className="flex flex-wrap gap-2">
              <Button kind="primary" busy={busy === "answer"} onClick={() => answer(null, "continue")}>
                Give it another ${payload.budget.toFixed(2)}
              </Button>
              <Button busy={busy === "answer"} onClick={() => answer(null, "stop")}>
                Stop it
              </Button>
            </div>
          ) : (
            <>
              <TextArea label="Your answer" value={text} onChange={setText} />
              <Button kind="primary" busy={busy === "answer"} disabled={!text.trim()} onClick={() => answer(null, text)}>
                Answer
              </Button>
            </>
          )}
        </div>
      ) : null}

      {decision.kind === "route" ? (
        <div className="mt-3 space-y-3">
          <p className="text-sm">Which project is this for?</p>
          <p className="text-sm text-muted">{String((payload.task as { outcome?: string } | undefined)?.outcome ?? decision.summary)}</p>
          <div className="flex flex-wrap gap-2">
            {decision.options.map((option) => (
              <Button key={option} kind={option === "none" ? "ghost" : "secondary"} busy={busy === option} onClick={() => answer(option)}>
                {option === "none" ? "None of these" : option}
              </Button>
            ))}
          </div>
        </div>
      ) : null}

      {error ? <div className="mt-3"><Alert>{error}</Alert></div> : null}
      {decision.task_id ? (
        <Link href={taskHref(decision.task_id)} className="mt-3 inline-block text-sm text-accent">
          Open the task ›
        </Link>
      ) : null}
    </Card>
  );
}
