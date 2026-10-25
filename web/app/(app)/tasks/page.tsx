"use client";

import { useState } from "react";

import { TaskCardView, type TaskCard } from "@/components/task-card";
import { Alert, Empty, Loading, PageHeader, Select } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";

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
  const [project, setProject] = useState("");
  const [status, setStatus] = useState("");
  const projects = useApi<{ name: string }[]>("projects");
  const tasks = useApi<TaskCard[]>("tasks", { project, status });
  useLive((event) => {
    if (event.type === "task") tasks.reload();
  });

  return (
    <>
      <PageHeader title="Tasks" subtitle="Every piece of work, live." />
      <div className="mb-4 grid grid-cols-2 gap-2">
        <Select label="Project" value={project} onChange={setProject} options={[["", "All projects"], ...(projects.data ?? []).map((p): [string, string] => [p.name, p.name])]} />
        <Select label="Status" value={status} onChange={setStatus} options={STATUSES} />
      </div>
      {tasks.error ? <Alert>{tasks.error}</Alert> : null}
      {tasks.loading && !tasks.data ? <Loading /> : null}
      {tasks.data?.length === 0 ? <Empty>No tasks match.</Empty> : null}
      <div className="space-y-2">
        {tasks.data?.map((task) => (
          <TaskCardView key={task.id} task={task} />
        ))}
      </div>
    </>
  );
}
