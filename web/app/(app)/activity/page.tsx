"use client";

import { useState } from "react";

import { Alert, Badge, Card, Code, Details, Empty, Field, Loading, PageHeader, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";

interface Row {
  id: number;
  at: string;
  project: string | null;
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
  const [project, setProject] = useState("");
  const [outcome, setOutcome] = useState("");
  const [since, setSince] = useState("");
  const projects = useApi<{ name: string }[]>("projects");
  const query = { project, outcome, since: since ? new Date(since).toISOString() : "" };
  const rows = useApi<Row[]>("audit", { ...query, limit: 200 });
  const csv = `/bff/audit.csv?${new URLSearchParams(Object.entries(query).filter(([, v]) => v) as [string, string][])}`;

  return (
    <>
      <PageHeader title="Activity" subtitle="Everything that changed, was refused or waited for you." />
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Select label="Project" value={project} onChange={setProject} options={[["", "All projects"], ...(projects.data ?? []).map((p): [string, string] => [p.name, p.name])]} />
        <Select label="What" value={outcome} onChange={setOutcome} options={OUTCOMES} />
        <Field label="Since" type="date" value={since} onChange={setSince} />
      </div>
      <a href={csv} className="mb-4 inline-flex min-h-11 items-center rounded-xl border border-line bg-surface px-4 text-sm font-medium" download>
        Export CSV{project ? ` for ${project}` : ""}
      </a>
      {rows.error ? <Alert>{rows.error}</Alert> : null}
      {rows.loading && !rows.data ? <Loading /> : null}
      {rows.data?.length === 0 ? <Empty>Nothing matches.</Empty> : null}
      <ol className="space-y-2">
        {rows.data?.map((row) => (
          <li key={row.id}>
            <Card className="space-y-1 py-3">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Badge tone={TONE[row.outcome] ?? "neutral"}>{OUTCOMES.find(([k]) => k === row.outcome)?.[1] ?? row.outcome}</Badge>
                {row.project ? <span className="font-medium">{row.project}</span> : null}
                {row.instance ? <span className="text-muted">on {row.instance}</span> : null}
                <span className="ml-auto text-xs text-muted">{new Date(row.at).toLocaleString()}</span>
              </div>
              {row.reason ? <p className="text-sm">{row.reason}</p> : null}
              <Details>
                <p className="mb-1 text-xs text-muted">Action: {row.tool}</p>
                {row.args ? <Code>{JSON.stringify(row.args, null, 2)}</Code> : null}
                {row.result ? <Code>{row.result}</Code> : null}
              </Details>
            </Card>
          </li>
        ))}
      </ol>
    </>
  );
}
