"use client";

import Link from "@/components/link";

import { plainText } from "@/components/markdown";
import { firstSentences, withoutTables } from "@/lib/markdown-table";
import { StatusBadge, money, timeAgo } from "@/components/ui";
import { taskHref } from "@/lib/client/routes";
import { place } from "@/lib/client/scope";

export interface TaskCard {
  id: string;
  files?: number; // how many files it made or changed
  project: string;
  client?: string | null;
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
  archived?: boolean;
  waiting_for_folder?: boolean;
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
        <span className="font-medium">{place(task.client, task.project)}</span>
        <StatusBadge status={task.status} />
        {task.archived ? <span className="text-xs text-muted">archived</span> : null}
        {task.phase && task.status !== "succeeded" ? <span className="text-xs text-muted">{PHASE[task.phase] ?? task.phase}</span> : null}
        <span className="ml-auto text-xs text-muted">{timeAgo(task.finished_at ?? task.created_at)}</span>
      </div>
      {task.waiting_for_folder ? <p className="mt-1 text-xs text-muted">Waiting for another task in this project to finish.</p> : null}
      <p className="mt-2 line-clamp-3 text-sm [overflow-wrap:anywhere]">{firstSentences(plainText(withoutTables(task.summary ?? task.outcome ?? "—")))}</p>
      {task.error && task.status !== "succeeded" ? <p className="mt-1 line-clamp-3 text-sm text-bad [overflow-wrap:anywhere]">{task.error}</p> : null}
      <p className="mt-2 text-xs text-muted">
        {task.turns} steps · {money(task.cost_usd)} · open the transcript ›
      </p>
    </Link>
  );
}
