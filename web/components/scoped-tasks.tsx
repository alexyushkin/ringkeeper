"use client";

import Link from "@/components/link";

import { TaskCardView, type TaskCard } from "@/components/task-card";
import { Alert, Empty, Loading } from "@/components/ui";
import { useLive, usePaged } from "@/lib/client/api";

const SHOWN = 20;

/** The latest tasks of one project or one client, with a link to all of them. */
export function ScopedTasks({ client, project }: { client?: string; project?: string }) {
  const tasks = usePaged<TaskCard>("tasks", { client, project }, SHOWN);
  useLive((event) => {
    if (event.type === "task") tasks.reload();
  });
  const all = tasks.items ?? [];
  const total = tasks.total ?? all.length;
  const query = new URLSearchParams({ ...(client ? { client } : {}), ...(project ? { project } : {}) }).toString();
  return (
    <div className="space-y-2">
      {tasks.error ? <Alert>{tasks.error}</Alert> : null}
      {tasks.loading && !tasks.items ? <Loading /> : null}
      {tasks.items && !all.length ? <Empty>No tasks yet. Ask for something on the Home page.</Empty> : null}
      {all.map((task) => (
        <TaskCardView key={task.id} task={task} />
      ))}
      {all.length ? (
        <Link href={`/tasks?${query}`} className="inline-block text-sm text-accent">
          {total > SHOWN ? `All ${total.toLocaleString()} tasks ›` : "Open in Tasks ›"}
        </Link>
      ) : null}
    </div>
  );
}

/** "3 tasks · 1 running": what a Tasks tab or section shows while folded. */
export function useTaskSummary(params: { client?: string; project?: string }): { count: number | undefined; running: number } {
  const tasks = usePaged<TaskCard>("tasks", params);
  const all = tasks.items;
  return { count: tasks.total, running: (all ?? []).filter((t) => t.status === "running" || t.status === "queued").length };
}
