"use client";

import { useState } from "react";

import { DecisionCard, type Decision } from "@/components/decision-card";
import { Alert, Empty, Loading, PageHeader, Select } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";
import { useScope } from "@/lib/client/scope";

/** Every action waiting for your OK, across conversations, in full: the SQL, the plan,
 * more money for a task. Questions ("which project?", a worker asking something) are
 * asked in their conversation instead. Telegram only gets summaries. */
const KINDS: [string, string][] = [
  ["", "Everything"],
  ["approve", "Live changes"],
  ["plan", "Plans"],
  ["question", "Spending caps"],
];

export default function ApprovalsPage() {
  const scope = useScope();
  const [kind, setKind] = useState("");
  const decisions = useApi<Decision[]>("decisions", { ...scope.query, kind, status: "pending", category: "approval", limit: 200 });
  useLive((event) => {
    if (event.type === "approval") decisions.reload();
  });
  // Production holds first; otherwise newest first, as the API lists them.
  const ordered = [...(decisions.data ?? [])].sort((a, b) => Number(b.production) - Number(a.production));

  return (
    <>
      <PageHeader title="Approvals" subtitle="Actions waiting for your OK before Ringkeeper goes on, from every conversation." />
      <div className="mb-4 grid grid-cols-2 gap-2">
        <Select label="Show" value={kind} onChange={setKind} options={KINDS} />
      </div>
      {decisions.error ? <Alert>{decisions.error}</Alert> : null}
      {decisions.loading && !decisions.data ? <Loading /> : null}
      {decisions.data?.length === 0 ? <Empty>Nothing is waiting for you. When an agent wants to change live client data, or send something to real people, it asks here first.</Empty> : null}
      <div className="space-y-4">
        {ordered.map((decision) => (
          <DecisionCard key={decision.id} decision={decision} onDone={decisions.reload} />
        ))}
      </div>
    </>
  );
}
