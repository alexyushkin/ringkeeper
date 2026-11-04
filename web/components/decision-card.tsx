"use client";

import { useConnectionTitles } from "@/components/connection-form";
import Link from "@/components/link";
import { Markdown } from "@/components/markdown";
import { useState } from "react";

import { Alert, Badge, Button, Card, Code, Details, Field, Select, TextArea, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { place, projectName } from "@/lib/client/scope";
import { taskHref } from "@/lib/client/routes";
import { KIND } from "@/lib/client/words";

export interface Decision {
  id: string;
  kind: string;
  status: string;
  summary: string;
  heading: string | null;
  options: string[];
  project: string | null;
  client: string | null;
  production: boolean;
  approval: boolean;
  instance: string | null;
  tool: string | null;
  args: Record<string, unknown> | null;
  payload: Record<string, unknown> | null;
  undo?: { possible: boolean; why: string | null; days: number } | null;
  task_id: string | null;
  thread_id: string | null;
  created_at: string;
  expires_at: string;
}

const NONE = "none";

/**
 * One thing waiting for you, with its buttons: a held call (what it wants to do, the
 * SQL, Approve / Deny with a note), a plan, a question, or which project a request is
 * for. The same card in the conversation, on the task page and in Approvals; answering
 * it in one place clears it everywhere (each list reloads on the live event).
 * `inline`: in a conversation or on a task page, without the link back to the task.
 */
export function DecisionCard({ decision, onDone, inline = false }: { decision: Decision; onDone: () => void; inline?: boolean }) {
  const title = useConnectionTitles();
  const [text, setText] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const payload = decision.payload ?? {};
  const destructive = typeof payload.destructive === "string" ? payload.destructive : null;

  async function trustAndApprove() {
    setBusy("trust");
    setError(null);
    try {
      await api("trusts", { json: { decision: decision.id } });
      await answer("approve", text);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    }
  }

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
    <Card className={destructive || decision.production ? "border-bad" : "border-warn"}>
      <div id={decision.id} className="flex flex-wrap items-center gap-2">
        <Badge tone={decision.kind === "approve" ? "bad" : decision.kind === "question" ? "accent" : "warn"}>
          {decision.kind === "route" && typeof payload.production === "string" ? "Go ahead?" : decision.kind === "approve" && !decision.production ? "Change" : (KIND[decision.kind] ?? decision.kind)}
        </Badge>
        {decision.project ? <span className="font-medium">{place(decision.client, decision.project)}</span> : null}
        {decision.production ? <Badge tone="bad">production</Badge> : null}
        {decision.instance ? <span className="text-sm text-muted">on {title(decision.instance)}</span> : null}
        <span className="ml-auto text-xs text-muted">{timeAgo(decision.created_at)}</span>
      </div>

      {decision.kind === "approve" ? (
        <div className="mt-3 space-y-3">
          <p className="text-sm">
            <strong>{decision.heading ?? "A change"}</strong> is waiting for your OK. Nothing has run yet.
          </p>
          {typeof payload.revert === "string" ? (
            <div className="space-y-2">
              <p className="text-sm">{payload.revert}</p>
              {typeof payload.what === "string" ? <p className="text-xs text-muted">It undoes: <span className="font-mono">{payload.what}</span></p> : null}
              {payload.changed_since === true ? (
                <Alert tone="warn">Something changed these since the original change. Reverting puts the old values back over what&apos;s there now.</Alert>
              ) : null}
            </div>
          ) : null}
          {typeof decision.args?.sql === "string" ? <Code>{decision.args.sql}</Code> : null}
          {decision.undo ? (
            <p className="text-xs text-muted">
              {decision.undo.possible ? `You can revert this for ${decision.undo.days} days after it runs (on its task page).` : `Can't be undone: ${decision.undo.why}`}
            </p>
          ) : null}
          <Details>
            <p className="mb-1 text-xs text-muted">Exactly what it will send ({decision.tool?.replace(/_/g, " ")})</p>
            <Code>{JSON.stringify(decision.args, null, 2)}</Code>
          </Details>
          {destructive ? (
            <div className="space-y-2">
              <Alert>
                This change {destructive}. {decision.undo?.possible ? "You can revert it for 7 days, but check it first." : "That can't be undone."}
              </Alert>
              <Field label={`Type ${decision.instance} to approve`} value={confirm} onChange={setConfirm} autoComplete="off" />
            </div>
          ) : null}
          <TextArea label="Note (optional)" value={text} onChange={setText} rows={2} hint="Goes to the agent with your answer, e.g. why not, or what to do instead." />
          <div className="flex flex-wrap gap-2">
            <span data-approve>
              <Button kind={destructive ? "danger" : "primary"} busy={busy === "approve"} disabled={Boolean(destructive) && confirm.trim() !== decision.instance} onClick={() => answer("approve", text)}>
                Approve
              </Button>
            </span>
            <span data-deny>
              <Button busy={busy === "deny"} onClick={() => answer("deny", text)}>
                Deny
              </Button>
            </span>
            {destructive || decision.tool === "revert_change" ? null : (
              <Button kind="ghost" busy={busy === "trust"} onClick={() => void trustAndApprove()}>
                Approve, and trust this in {projectName(decision.project)} for 1 hour
              </Button>
            )}
          </div>
        </div>
      ) : null}

      {decision.kind === "plan" ? (
        <div className="mt-3 space-y-3">
          <h3 className="font-medium">{String(payload.title ?? "A plan")}</h3>
          <Code>{String(payload.plan ?? "")}</Code>
          <Details label="Show the agent's instructions">
            <Code>{String(payload.worker_prompt ?? "")}</Code>
          </Details>
          <TextArea label="Note (optional)" value={text} onChange={setText} rows={2} hint="Sent with your answer, e.g. what to change." />
          <div className="flex flex-wrap gap-2">
            <span data-approve>
              <Button kind="primary" busy={busy === "approve"} onClick={() => answer("approve", text)}>
                Approve plan
              </Button>
            </span>
            <span data-deny>
              <Button busy={busy === "deny"} onClick={() => answer("deny", text)}>
                Ask for changes
              </Button>
            </span>
          </div>
        </div>
      ) : null}

      {decision.kind === "question" ? (
        <div className="mt-3 space-y-3">
          <Markdown text={String(payload.question ?? decision.summary)} />
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

      {decision.kind === "route" ? <RouteQuestion decision={decision} busy={busy} onAnswer={(choice) => void answer(choice)} /> : null}

      {error ? (
        <div className="mt-3">
          <Alert>{error}</Alert>
        </div>
      ) : null}
      {decision.task_id && !inline ? (
        <Link href={taskHref(decision.task_id)} className="mt-3 inline-block text-sm text-accent">
          Open the task ›
        </Link>
      ) : null}
    </Card>
  );
}

/** "Which project is this for?" with a button per likely project, any other project,
 * or none; or, when Ringkeeper is sure of the project but it would use production,
 * whether to go ahead. */
function RouteQuestion({ decision, busy, onAnswer }: { decision: Decision; busy: string | null; onAnswer: (choice: string) => void }) {
  const payload = decision.payload ?? {};
  const task = (payload.task ?? {}) as { outcome?: string };
  const production = typeof payload.production === "string" ? payload.production : null;
  const projects = useApi<{ name: string }[]>(production ? null : "projects");
  const [other, setOther] = useState("");
  const offered = decision.options.filter((o) => o !== NONE);
  const rest = (projects.data ?? []).map((p) => p.name).filter((name) => !offered.includes(name));

  if (production) {
    const project = offered[0] ?? decision.project ?? "";
    return (
      <div className="mt-3 space-y-3">
        <p className="text-sm">
          This would use <strong>{production}</strong> in {projectName(project)}: live client data. Go ahead?
        </p>
        {task.outcome ? <p className="text-sm text-muted">{task.outcome}</p> : null}
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy === project} onClick={() => onAnswer(project)}>
            Go ahead
          </Button>
          <Button kind="ghost" busy={busy === NONE} onClick={() => onAnswer(NONE)}>
            Don&apos;t do it
          </Button>
        </div>
      </div>
    );
  }
  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm">Which project is this for?</p>
      {task.outcome ? <p className="text-sm text-muted">{task.outcome}</p> : null}
      <div className="flex flex-wrap gap-2">
        {offered.map((option) => (
          <Button key={option} busy={busy === option} onClick={() => onAnswer(option)}>
            {option}
          </Button>
        ))}
        <Button kind="ghost" busy={busy === NONE} onClick={() => onAnswer(NONE)}>
          None of these
        </Button>
      </div>
      {rest.length ? (
        <div className="flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1">
            <Select label="Another project" value={other} onChange={setOther} options={[["", "Choose…"], ...rest.map((name): [string, string] => [name, projectName(name)])]} />
          </div>
          <Button disabled={!other} busy={busy === other} onClick={() => onAnswer(other)}>
            Use it
          </Button>
        </div>
      ) : null}
    </div>
  );
}
