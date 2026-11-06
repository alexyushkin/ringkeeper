"use client";

import { useState } from "react";

import { useConnectionTitles } from "@/components/connection-form";
import { Alert, Badge, Card, Code, Details, Empty, Field, Loading, PageHeader, Select } from "@/components/ui";
import { usePaged } from "@/lib/client/api";
import { ListTotal, LoadMore } from "@/components/tidy";
import { place, useScope } from "@/lib/client/scope";

interface Row {
  id: number;
  at: string;
  project: string | null;
  client: string | null;
  instance: string | null;
  tool: string | null;
  outcome: string;
  reason: string | null;
  args: Record<string, unknown> | null;
  result: string | null;
}

const OUTCOMES: [string, string][] = [
  ["", "Everything"],
  ["executed", "Changes made"],
  ["held", "Held for approval"],
  ["refused", "Refused"],
  ["failed", "Failed"],
  ["answered", "Answers you gave"],
  ["changed", "Your settings changes"],
];
const TONE: Record<string, "good" | "warn" | "bad" | "accent" | "neutral"> = {
  executed: "good",
  held: "warn",
  refused: "bad",
  failed: "bad",
  answered: "accent",
  changed: "neutral",
};

/** The audit log: every change, refusal and answer. Export it per client as CSV. */
export default function ActivityPage() {
  const scope = useScope();
  const [outcome, setOutcome] = useState("");
  const [since, setSince] = useState("");
  const [words, setWords] = useState("");
  const query = { client: scope.client, project: scope.project, outcome, since: since ? new Date(since).toISOString() : "", q: words.trim() };
  const rows = usePaged<Row>("audit", query);
  const connection = useConnectionTitles();
  const csv = `/bff/audit.csv?${new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][])}`;

  return (
    <>
      <PageHeader title="Activity" subtitle="Everything that changed, was refused or waited for you." />
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Select label="What" value={outcome} onChange={setOutcome} options={OUTCOMES} />
        <Field label="Since" type="date" value={since} onChange={setSince} />
        <div className="sm:col-span-2">
          <Field label="Search activity" value={words} onChange={setWords} placeholder="Words from what happened, what was sent or what came back" />
        </div>
      </div>
      <a href={csv} className="mb-4 inline-flex min-h-11 items-center rounded-xl border border-line bg-surface px-4 text-sm font-medium" download>
        Export CSV{scope.project || scope.client ? ` for ${place(scope.client, scope.project)}` : ""}
      </a>
      <ListTotal total={rows.total} what={["entry", "entries"]} searching={Boolean(words.trim())} archived={false} />
      {rows.error ? <Alert>{rows.error}</Alert> : null}
      {rows.loading && !rows.items ? <Loading /> : null}
      {rows.items?.length === 0 ? <Empty>Nothing here yet. Changes, refusals and your answers are listed here as they happen; if you set a filter, clear it.</Empty> : null}
      <ol className="space-y-2">
        {rows.items?.map((row) => (
          <li key={row.id}>
            <Card className="space-y-1 py-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={TONE[row.outcome] ?? "neutral"}>{OUTCOMES.find(([k]) => k === row.outcome)?.[1] ?? row.outcome}</Badge>
                {row.project ? <span className="font-medium">{place(row.client, row.project)}</span> : null}
                {row.instance ? <span className="text-muted">on {connection(row.instance)}</span> : null}
                <span className="ml-auto text-xs text-muted">{new Date(row.at).toLocaleString()}</span>
              </div>
              {row.reason ? <p className="text-sm">{row.reason}</p> : null}
              <Details>
                <p className="mb-1 text-xs text-muted">Exactly what was sent ({row.tool?.replace(/_/g, " ")})</p>
                {row.args ? <Code>{JSON.stringify(row.args, null, 2)}</Code> : null}
                {row.result ? <Code>{row.result}</Code> : null}
              </Details>
            </Card>
          </li>
        ))}
      </ol>
      <LoadMore list={rows} />
    </>
  );
}
