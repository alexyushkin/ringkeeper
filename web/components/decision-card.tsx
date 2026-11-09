"use client";

import { useConnectionTitles } from "@/components/connection-form";
import Link from "@/components/link";
import { Markdown } from "@/components/markdown";
import { useState } from "react";

import { Alert, Badge, Button, Card, Code, Details, Field, Select, TextArea, Toggle, clock, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientName, place, projectName } from "@/lib/client/scope";
import { taskHref } from "@/lib/client/routes";
import { KIND } from "@/lib/client/words";

export interface Decision {
  id: string;
  kind: string;
  status: string;
  summary: string;
  heading: string | null;
  money?: { amount: string; counterparty: string; account: string; who?: string; which?: string; counterparty_id?: string; account_id?: string } | null;
  trustable?: boolean;
  declares?: string | null;
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
  snoozed_until?: string | null;
  answer?: Record<string, unknown> | null;
}

interface Estimate {
  model: string;
  low: number | null;
  high: number | null;
  based_on: number;
  spent: number;
  limit: number | null;
  subscription: boolean;
}

const NONE = "none";
const SPARE = 5 * 60_000; // a snooze ends at least this long before the approval runs out

/** Snooze choices that end before it runs out: in an hour, in three, tomorrow at 9. */
function snoozes(expires: string): [string, Date][] {
  const now = Date.now();
  const morning = new Date();
  morning.setDate(morning.getDate() + 1);
  morning.setHours(9, 0, 0, 0);
  const all: [string, Date][] = [
    ["1 hour", new Date(now + 3_600_000)],
    ["3 hours", new Date(now + 3 * 3_600_000)],
    ["Tomorrow 9 AM", morning],
  ];
  return all.filter(([, at]) => at.getTime() < new Date(expires).getTime() - SPARE);
}

const dollars = (n: number) => `$${n.toFixed(2)}`;

/** The model that would do the work, and what tasks like it have cost. */
function PlanCost({ estimate }: { estimate: Estimate }) {
  return (
    <dl data-estimate className="grid gap-1 rounded-md border border-line p-3 text-sm sm:grid-cols-[auto_1fr] sm:gap-x-4">
      <dt className="text-muted">Model</dt>
      <dd className="font-mono text-xs leading-5">{estimate.model}</dd>
      <dt className="text-muted">Likely cost</dt>
      <dd>
        {estimate.low !== null && estimate.high !== null
          ? estimate.low === estimate.high
            ? `About ${dollars(estimate.low)}`
            : `${dollars(estimate.low)} to ${dollars(estimate.high)}`
          : "No finished task like this yet to go by"}
        {estimate.based_on ? <span className="text-muted"> (from {estimate.based_on} like it, plan to review)</span> : null}
      </dd>
      {estimate.spent ? (
        <>
          <dt className="text-muted">So far</dt>
          <dd>{dollars(estimate.spent)} on the plan</dd>
        </>
      ) : null}
      {estimate.limit ? (
        <>
          <dt className="text-muted">Limit</dt>
          <dd>It stops at {dollars(estimate.limit)} and asks before spending more</dd>
        </>
      ) : null}
      {estimate.subscription ? <dd className="text-xs text-muted sm:col-span-2">On your Claude subscription, this counts toward its usage limits instead of being billed.</dd> : null}
    </dl>
  );
}

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

  async function snooze(until: Date | null) {
    setBusy("snooze");
    setError(null);
    try {
      await api(`decisions/${decision.id}/snooze`, { json: { until: until ? until.toISOString() : null } });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const snoozed = decision.snoozed_until && new Date(decision.snoozed_until).getTime() > Date.now() ? decision.snoozed_until : null;
  const canSnooze = decision.approval && decision.status === "pending" && !decision.answer;

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
      {snoozed ? (
        <div data-snoozed className="mt-2 flex flex-wrap items-center gap-2 text-sm text-muted">
          Snoozed until {clock(snoozed)}: it isn&apos;t in Approvals or notifications till then.
          <Button kind="ghost" busy={busy === "snooze"} onClick={() => void snooze(null)}>
            Wake it now
          </Button>
        </div>
      ) : null}

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
          {decision.money ? (
            <dl className="rounded-md border border-line p-3">
              <dd className="text-2xl font-semibold">{decision.money.amount}</dd>
              <dt className="mt-2 text-xs text-muted">{decision.money.who ?? "To or from"}</dt>
              <dd className="text-lg font-medium">
                {decision.money.counterparty}
                {decision.money.counterparty_id ? <span className="ml-2 text-xs font-normal text-muted">ID {decision.money.counterparty_id}</span> : null}
              </dd>
              <dt className="mt-2 text-xs text-muted">{decision.money.which ?? "Account"}</dt>
              <dd className="text-lg font-medium">
                {decision.money.account}
                {decision.money.account_id ? <span className="ml-2 text-xs font-normal text-muted">ID {decision.money.account_id}</span> : null}
              </dd>
            </dl>
          ) : null}
          {decision.declares ? (
            <div data-declares className="rounded-md border border-line p-3">
              <p className="text-xs text-muted">Approving declares this to the vendor, in your name</p>
              <p className="mt-1 text-lg font-medium">{decision.declares}</p>
              <p className="mt-1 text-xs text-muted">If that isn&apos;t true, deny this.</p>
            </div>
          ) : null}
          {typeof decision.args?.sql === "string" ? <Code>{decision.args.sql}</Code> : null}
          {typeof decision.args?.sql === "string" && decision.args.params ? <Code>{`Values: ${JSON.stringify(decision.args.params)}`}</Code> : null}
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
            {destructive || decision.tool === "revert_change" || decision.trustable === false ? null : (
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
          {payload.estimate ? <PlanCost estimate={payload.estimate as Estimate} /> : null}
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
          {payload.client_cap ? (
            <ClientCapAnswer cap={payload.client_cap as ClientCap} busy={busy === "answer"} onAnswer={(reply) => void answer(null, reply)} />
          ) : typeof payload.budget === "number" ? (
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

      {canSnooze && !snoozed && snoozes(decision.expires_at).length ? (
        <div data-snooze className="mt-3 flex flex-wrap items-center gap-1 text-sm text-muted">
          Not now? Snooze it:
          {snoozes(decision.expires_at).map(([label, at]) => (
            <Button key={label} kind="ghost" busy={busy === "snooze"} onClick={() => void snooze(at)}>
              {label}
            </Button>
          ))}
        </div>
      ) : null}

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
  const [remember, setRemember] = useState(false);
  const offered = decision.options.filter((o) => o !== NONE);
  const rest = (projects.data ?? []).map((p) => p.name).filter((name) => !offered.includes(name));

  if (production) {
    const project = offered[0] ?? decision.project ?? "";
    return (
      <div className="mt-3 space-y-3">
        <p className="text-sm">
          This would use <strong>{production}</strong> in {projectName(project)}: live client data. Go ahead?
        </p>
        {task.outcome ? <Markdown text={task.outcome} className="text-sm text-muted" /> : null}
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
  const why = typeof payload.reason === "string" ? payload.reason : "";
  const offerFor = typeof payload.offer_default === "string" ? payload.offer_default : null;
  async function pick(option: string) {
    if (offerFor && remember && option !== NONE) await api(`clients/${offerFor}/default-project`, { method: "PUT", json: { project: option } }).catch(() => undefined);
    onAnswer(option);
  }
  return (
    <div className="mt-3 space-y-3">
      <p className="text-sm">Which project is this for?{why ? ` ${why}` : ""}</p>
      {task.outcome ? <Markdown text={task.outcome} className="text-sm text-muted" /> : null}
      {offerFor ? <Toggle label={`Use it for ${clientName(offerFor)} from now on`} checked={remember} onChange={setRemember} hint="For requests any of their projects could do, like their email or Google. You can change it on the client's page." /> : null}
      <div className="flex flex-wrap gap-2">
        {offered.map((option) => (
          <Button key={option} busy={busy === option} onClick={() => void pick(option)}>
            {projectName(option)}
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
          <Button disabled={!other} busy={busy === other} onClick={() => void pick(other)}>
            Use it
          </Button>
        </div>
      ) : null}
    </div>
  );
}

interface ClientCap {
  name: string;
  used: number;
  cap: number;
}

/** Over a client's monthly cap: run this task anyway, just this once; raise the cap (above
 * what the client has used); or don't run it. Both ways of going on are in Activity. */
function ClientCapAnswer({ cap, busy, onAnswer }: { cap: ClientCap; busy: boolean; onAnswer: (reply: string) => void }) {
  const [raising, setRaising] = useState(false);
  const [amount, setAmount] = useState(String(Math.ceil(Math.max(cap.cap * 1.5, cap.used + 1))));
  const value = Number(amount);
  const valid = Number.isFinite(value) && value > cap.used;
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} onClick={() => onAnswer("run once")}>
          Run anyway this once
        </Button>
        <Button busy={busy} onClick={() => setRaising(true)}>
          Raise the cap
        </Button>
        <Button kind="ghost" busy={busy} onClick={() => onAnswer("stop")}>
          Don&apos;t run it
        </Button>
      </div>
      {raising ? (
        <div className="flex flex-wrap items-end gap-2">
          <div className="max-w-48">
            <Field label={`New monthly cap for ${cap.name} ($)`} value={amount} onChange={setAmount} inputMode="decimal" />
          </div>
          <Button kind="primary" busy={busy} disabled={!valid} onClick={() => onAnswer(`raise:${value}`)}>
            Raise it and run
          </Button>
          {!valid ? <p className="text-sm text-bad">Above ${cap.used.toFixed(2)}, what {cap.name} has used this month.</p> : null}
        </div>
      ) : null}
    </div>
  );
}
