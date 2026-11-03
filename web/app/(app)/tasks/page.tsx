"use client";

import { useState } from "react";

import { TaskCardView, type TaskCard } from "@/components/task-card";
import { BulkBar, ListTools, SelectBox, useSelection } from "@/components/tidy";
import { Alert, Button, Empty, Loading, PageHeader, Select } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { useScope } from "@/lib/client/scope";

const STATUSES: [string, string][] = [
  ["", "Any status"],
  ["running", "Working"],
  ["queued", "Waiting to start"],
  ["waiting", "Waiting for you"],
  ["needs_input", "Has a question"],
  ["succeeded", "Done"],
  ["failed", "Failed"],
  ["stopped", "Stopped"],
];

export default function TasksPage() {
  const scope = useScope();
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const tasks = useApi<TaskCard[]>("tasks", { ...scope.query, status, q: query.trim() || undefined, archived: archived || undefined });
  const old = useApi<{ count: number; days: number }>("cleanup/old-tasks");
  const [tidied, setTidied] = useState<string | null>(null);
  const pick = useSelection();
  useLive((event) => {
    if (event.type === "task") {
      tasks.reload();
      old.reload();
    }
  });

  async function archiveOld() {
    const done = await api<{ changed: number }>("cleanup/old-tasks", { json: {} });
    setTidied(`Archived ${done.changed} task${done.changed === 1 ? "" : "s"}. They're under Show archived, and search still finds them.`);
    tasks.reload();
    old.reload();
  }

  return (
    <>
      <PageHeader title="Tasks" subtitle="Every piece of work, live." />
      <div className="mb-4 grid grid-cols-2 gap-2">
        <Select label="Status" value={status} onChange={setStatus} options={STATUSES} />
      </div>
      {old.data?.count && !archived ? (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-xl border border-line bg-surface p-3 text-sm">
          <span className="text-muted">
            {old.data.count} failed or stopped task{old.data.count === 1 ? "" : "s"} older than {old.data.days} days.
          </span>
          <Button onClick={archiveOld}>Archive failed tasks older than {old.data.days} days</Button>
        </div>
      ) : null}
      {tidied ? <Alert tone="good">{tidied}</Alert> : null}
      <ListTools query={query} onQuery={setQuery} archived={archived} onArchived={(on) => { setArchived(on); pick.clear(); }} what="tasks" />
      <BulkBar kind="task" ids={[...pick.selected]} archivedView={archived} onDone={tasks.reload} onClear={pick.clear} />
      {tasks.error ? <Alert>{tasks.error}</Alert> : null}
      {tasks.loading && !tasks.data ? <Loading /> : null}
      {tasks.data?.length === 0 ? <Empty>{query || archived || status ? "No tasks match. Change the status or clear the search." : "No tasks yet. Ask for something on Home and the work shows up here."}</Empty> : null}
      <div className="space-y-2">
        {tasks.data?.map((task) => (
          <div key={task.id} className="flex items-start gap-3">
            <span className="pt-5">
              <SelectBox checked={pick.has(task.id)} onChange={() => pick.toggle(task.id)} label={`Select the ${task.project} task`} />
            </span>
            <div className="min-w-0 flex-1">
              <TaskCardView task={task} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}
