"use client";

import Link from "next/link";

import { StatusBadge, money, timeAgo } from "@/components/ui";
import { taskHref } from "@/lib/client/routes";

export interface TaskCard {
  id: string;
  project: string;
  status: string;
  kind: string | null;
  phase: string | null;
  outcome: string | null;
  summary: string | null;
  error: string | null;
  thread_id: string | null;
  created_at: string;
  finished_at: string | null;
  turns: number;
  cost_usd: number | null;
}

const PHASE: Record<string, string> = {
  planning: "Planning",
  awaiting_plan: "Plan ready for you",
  building: "Building",
  reviewing: "Reviewing the work",
  done: "Done",
};

export function TaskCardView({ task }: { task: TaskCard }) {
  return (
    <Link href={taskHref(task.id)} className="block rounded-2xl border border-line bg-surface p-4 hover:border-accent">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{task.project}</span>
        <StatusBadge status={task.status} />
        {task.phase && task.status !== "succeeded" ? <span className="text-xs text-muted">{PHASE[task.phase] ?? task.phase}</span> : null}
        <span className="ml-auto text-xs text-muted">{timeAgo(task.finished_at ?? task.created_at)}</span>
      </div>
      <p className="mt-2 text-sm">{task.summary ?? task.outcome ?? "—"}</p>
      {task.error && task.status !== "succeeded" ? <p className="mt-1 text-sm text-bad">{task.error}</p> : null}
      <p className="mt-2 text-xs text-muted">
        {task.turns} steps · {money(task.cost_usd)} · open the transcript ›
      </p>
    </Link>
  );
}
