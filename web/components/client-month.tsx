"use client";

import Link from "@/components/link";
import { useState } from "react";

import { Alert, Button, Empty, Field, Loading, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface TaskLine {
  when: string;
  project: string;
  title: string;
  status: string;
  result: string;
  cost_usd: number | null;
}

interface Month {
  name: string;
  month: string;
  label: string;
  projects: number;
  summary: string[];
  cost_usd: number;
  estimated: boolean;
  cap_usd: number | null;
  tasks: TaskLine[];
  mail: { connection: string; title: string; project: string }[];
}

export function money(value: number): string {
  return value.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function thisMonth(): string {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** What Ringkeeper did for one client in a month and what it cost, the report to send them
 * (Markdown, PDF, CSV), and the report as an email, held in Approvals until you approve it. */
export function ClientMonth({ slug }: { slug: string }) {
  const [month, setMonth] = useState(thisMonth());
  const [cost, setCost] = useState(false);
  const found = useApi<Month>(`clients/${slug}/month`, { month });
  const data = found.data;
  const query = (extra: Record<string, string> = {}) => new URLSearchParams({ month, ...extra }).toString();
  const withCost: Record<string, string> = cost ? { cost: "true" } : {};

  return (
    <div className="space-y-4">
      <div className="max-w-xs">
        <Field label="Month" type="month" value={month} onChange={(value) => value && setMonth(value)} />
      </div>
      {found.error ? <Alert>{found.error}</Alert> : null}
      {found.loading && !data ? <Loading /> : null}
      {data ? (
        <>
          <ul className="space-y-1 text-sm">
            {data.summary.map((line) => (
              <li key={line}>{line}</li>
            ))}
          </ul>
          {data.estimated && data.cost_usd > 0 ? (
            <p className="text-xs text-muted">The AI cost is an estimate: Claude runs on your subscription here, so it isn&apos;t billed per task.</p>
          ) : null}
          {data.cap_usd !== null ? (
            <p className="text-sm text-muted">
              Monthly cap: {money(data.cap_usd)}. New tasks for {data.name} stop once a month&apos;s AI cost reaches it.
            </p>
          ) : null}
          {data.tasks.length ? (
            <ul className="space-y-2">
              {data.tasks.map((task, i) => (
                <li key={`${task.when}-${i}`} className="rounded-xl border border-line p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{task.title}</span>
                    <span className="text-xs text-muted">
                      {new Date(task.when).toLocaleDateString(undefined, { month: "short", day: "numeric" })} · {task.project} · {task.status}
                      {task.cost_usd !== null ? ` · ${money(task.cost_usd)}` : ""}
                    </span>
                  </div>
                  {task.result ? <p className="mt-1 text-muted">{task.result}</p> : null}
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No tasks for {data.name} in {data.label}.</Empty>
          )}
          <div className="space-y-2 rounded-xl border border-line p-3">
            <h3 className="font-medium">The report for {data.name}</h3>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={cost} onChange={(e) => setCost(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
              Include the AI cost
            </label>
            <div className="flex flex-wrap gap-2">
              <a href={`/bff/clients/${slug}/month.pdf?${query(withCost)}`} download className="inline-flex min-h-11 items-center rounded-xl border border-line bg-surface px-4 text-sm font-medium">
                Download PDF
              </a>
              <a href={`/bff/clients/${slug}/month.md?${query(withCost)}`} download className="inline-flex min-h-11 items-center rounded-xl border border-line bg-surface px-4 text-sm font-medium">
                Download Markdown
              </a>
              <a href={`/bff/clients/${slug}/month.csv?${query()}`} download className="inline-flex min-h-11 items-center rounded-xl border border-line bg-surface px-4 text-sm font-medium">
                Download tasks as CSV
              </a>
            </div>
            <EmailIt slug={slug} data={data} cost={cost} />
          </div>
        </>
      ) : null}
    </div>
  );
}

function EmailIt({ slug, data, cost }: { slug: string; data: Month; cost: boolean }) {
  const [to, setTo] = useState("");
  const [connection, setConnection] = useState(data.mail[0]?.connection ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [held, setHeld] = useState(false);

  if (!data.mail.length) {
    return <p className="text-sm text-muted">To email it from here, link a mail connection (SMTP, Resend, a mailbox or Gmail) to one of {data.name}&apos;s projects.</p>;
  }

  async function send() {
    setBusy(true);
    setError(null);
    setHeld(false);
    try {
      await api(`clients/${slug}/month/email`, { json: { month: data.month, to: to.trim(), connection, cost } });
      setHeld(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 pt-2">
      <Field label={`Email it to ${data.name}`} type="email" value={to} onChange={setTo} placeholder="name@example.com" />
      {data.mail.length > 1 ? (
        <Select label="Send from" value={connection} onChange={setConnection} options={data.mail.map((m): [string, string] => [m.connection, `${m.title} (${m.project})`])} />
      ) : (
        <p className="text-sm text-muted">From {data.mail[0]?.title}.</p>
      )}
      <Button kind="primary" busy={busy} disabled={!to.includes("@") || !connection} onClick={send}>
        Draft the email
      </Button>
      {held ? (
        <Alert tone="good">
          The email waits in <Link href="/approvals" className="underline">Approvals</Link>. Nothing is sent until you approve it there.
        </Alert>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** A client's monthly cap on AI cost: new tasks for them stop once a month's cost reaches it. */
export function MonthlyCap({ slug, name, cap, onSaved }: { slug: string; name: string; cap: number | null; onSaved: () => void }) {
  const [typed, setTyped] = useState(cap !== null ? String(cap) : "");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const value = typed.trim() ? Number(typed) : null;
  const valid = value === null || (Number.isFinite(value) && value > 0);

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await api(`clients/${slug}/cap`, { method: "PUT", json: { cap_usd: value } });
      setSaved(true);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-3">
      <Field
        label="Monthly cap (USD)"
        value={typed}
        onChange={setTyped}
        inputMode="decimal"
        placeholder="No cap"
        hint={`New tasks for ${name} stop once a month's AI cost reaches it; tasks already running finish. Leave it empty for no cap.`}
      />
      <Button kind="primary" disabled={!valid} onClick={save}>
        Save cap
      </Button>
      {!valid ? <Alert>Type an amount in dollars, like 50.</Alert> : null}
      {saved ? <Alert tone="good">{value === null ? "No cap now." : `Saved: ${money(value)} a month.`}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
