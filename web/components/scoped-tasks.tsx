"use client";

import Link from "@/components/link";

import { TaskCardView, type TaskCard } from "@/components/task-card";
import { Alert, Empty, Loading } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";

const SHOWN = 20;

/** The latest tasks of one project or one client, with a link to all of them. */
export function ScopedTasks({ client, project }: { client?: string; project?: string }) {
  const tasks = useApi<TaskCard[]>("tasks", { client, project });
  useLive((event) => {
    if (event.type === "task") tasks.reload();
  });
  const all = tasks.data ?? [];
  const query = new URLSearchParams({ ...(client ? { client } : {}), ...(project ? { project } : {}) }).toString();
  return (
    <div className="space-y-2">
      {tasks.error ? <Alert>{tasks.error}</Alert> : null}
      {tasks.loading && !tasks.data ? <Loading /> : null}
      {tasks.data && !all.length ? <Empty>No tasks yet. Ask for something on the Home page.</Empty> : null}
      {all.slice(0, SHOWN).map((task) => (
        <TaskCardView key={task.id} task={task} />
      ))}
      {all.length ? (
        <Link href={`/tasks?${query}`} className="inline-block text-sm text-accent">
          {all.length > SHOWN ? `All ${all.length} tasks ›` : "Open in Tasks ›"}
        </Link>
      ) : null}
    </div>
  );
}

/** "3 tasks · 1 running": what a Tasks tab or section shows while folded. */
export function useTaskSummary(params: { client?: string; project?: string }): { count: number | undefined; running: number } {
  const tasks = useApi<TaskCard[]>("tasks", params);
  const all = tasks.data;
  return { count: all?.length, running: (all ?? []).filter((t) => t.status === "running" || t.status === "queued").length };
}
