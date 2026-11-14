"use client";

import { useEffect, useState } from "react";

import { TaskCardView, type TaskCard } from "@/components/task-card";
import { BulkBar, ListTools, ListTotal, LoadMore, SelectBox, useSelection } from "@/components/tidy";
import { Alert, Button, Empty, Loading, PageHeader, Select } from "@/components/ui";
import { RESYNC, api, useApi, useLive, usePaged } from "@/lib/client/api";
import { ScopeNote } from "@/components/scope-switcher";
import { clientName, projectName, useScope } from "@/lib/client/scope";

const STATUSES: [string, string][] = [
  ["", "Any status"],
  ["running", "Working"],
  ["queued", "Waiting to start"],
  ["waiting", "Waiting for you"],
  ["needs_input", "Has a question"],
  ["succeeded", "Finished"],
  ["not_done", "Not done"],
  ["failed", "Failed"],
  ["stopped", "Stopped"],
];

/** Home's last-day counts link here: a client, an outcome and a window, the owner's own work. */
interface Linked {
  client?: string;
  outcome?: "broke" | "not_done";
  since?: string;
  real?: boolean;
}

function linkedFrom(search: string): Linked {
  const params = new URLSearchParams(search);
  const outcome = params.get("outcome");
  return {
    client: params.get("client") ?? undefined,
    outcome: outcome === "broke" || outcome === "not_done" ? outcome : undefined,
    since: params.get("since") ?? undefined,
    real: params.get("real") === "1" || undefined,
  };
}

const OUTCOME_WORDS = { broke: "failed", not_done: "not done" } as const;

export default function TasksPage() {
  const scope = useScope();
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const [linked, setLinked] = useState<Linked>({});
  useEffect(() => setLinked(linkedFrom(window.location.search)), []);
  const narrowed = Boolean(linked.since || linked.outcome);
  const tasks = usePaged<TaskCard>("tasks", {
    ...scope.query,
    ...(linked.client ? { client: linked.client } : {}),
    status,
    q: query.trim() || undefined,
    archived: archived || undefined,
    outcome: linked.outcome,
    since: linked.since,
    real: linked.real,
  });
  const old = useApi<{ count: number; days: number }>("cleanup/old-tasks");
  const [tidied, setTidied] = useState<string | null>(null);
  const pick = useSelection();
  useLive((event) => {
    if (event.type === "task" || event.type === RESYNC) {
      tasks.reload();
      old.reload();
    }
  });

  async function archiveOld() {
    const done = await api<{ changed: number }>("cleanup/old-tasks", { json: {} });
    setTidied(`Archived ${done.changed} task${done.changed === 1 ? "" : "s"}. They're under Archived, and search still finds them.`);
    tasks.reload();
    old.reload();
  }

  return (
    <>
      <PageHeader title="Tasks" subtitle="Every piece of work, live." />
      <ScopeNote />
      {narrowed ? (
        <p className="mb-3 flex flex-wrap items-center gap-2 text-sm text-muted">
          Showing {linked.outcome ? `${OUTCOME_WORDS[linked.outcome]} tasks` : "every task"}{linked.client ? ` for ${clientName(linked.client)}` : ""}
          {linked.since ? ` since ${new Date(linked.since).toLocaleString(undefined, { weekday: "short", hour: "numeric", minute: "2-digit" })}` : ""}
          {linked.real ? ", not counting Check my setup's or the demo's" : ""}.
          <Button
            kind="ghost"
            onClick={() => {
              setLinked({});
              window.history.replaceState(null, "", "/tasks");
            }}
          >
            Show all
          </Button>
        </p>
      ) : null}
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
      <ListTotal total={tasks.total} what={["task", "tasks"]} searching={Boolean(query.trim())} archived={archived} />
      {tasks.error ? <Alert>{tasks.error}</Alert> : null}
      {tasks.loading && !tasks.items ? <Loading /> : null}
      {tasks.items?.length === 0 ? <Empty>{query || archived || status ? "No tasks match. Change the status or clear the search." : "No tasks yet. Ask for something on Home and the work shows up here."}</Empty> : null}
      <div className="space-y-2">
        {tasks.items?.map((task) => (
          <div key={task.id} className="flex items-start gap-3">
            <span className="pt-5">
              <SelectBox checked={pick.has(task.id)} onChange={() => pick.toggle(task.id)} label={`Select the ${projectName(task.project)} task`} />
            </span>
            <div className="min-w-0 flex-1">
              <TaskCardView task={task} />
            </div>
          </div>
        ))}
      </div>
      <LoadMore list={tasks} />
    </>
  );
}
