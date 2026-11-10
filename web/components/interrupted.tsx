"use client";

import Link from "@/components/link";
import { useState } from "react";

import { Alert, Button, timeAgo } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { taskHref } from "@/lib/client/routes";
import { place } from "@/lib/client/scope";

interface Interrupted {
  id: string;
  project: string;
  client: string | null;
  prompt: string;
  finished_at: string | null;
}

/** Home: tasks a restart or an update cut short, each with Resume, and Resume all. */
export function InterruptedList() {
  const list = useApi<Interrupted[]>("tasks/interrupted");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useLive((event) => {
    if (event.type === "task") list.reload();
  });
  if (!list.data?.length) return null;

  async function resume(path: string, key: string) {
    setBusy(key);
    setError(null);
    try {
      await api(path, { method: "POST" });
      list.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const many = list.data.length > 1;
  return (
    <section aria-label="Cut short by a restart" className="mt-6">
      <div className="mb-2 flex items-center gap-2">
        <h2 className="text-sm font-semibold text-muted">Cut short by a restart</h2>
        {many ? (
          <Button kind="ghost" busy={busy === "all"} onClick={() => void resume("tasks/interrupted/resume", "all")} className="ml-auto">
            Resume all
          </Button>
        ) : null}
      </div>
      <p className="mb-2 text-sm text-muted">Ringkeeper restarted while {many ? "these were" : "this was"} running. Resume picks up where it stopped.</p>
      <ul className="space-y-2">
        {list.data.map((task) => (
          <li key={task.id} className="flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-surface p-3">
            <Link href={taskHref(task.id)} className="min-w-0 flex-1 hover:text-accent">
              <span className="block truncate text-sm font-medium">{task.prompt.split("\n")[0]}</span>
              <span className="block text-xs text-muted">
                {place(task.client, task.project)}
                {task.finished_at ? ` · ${timeAgo(task.finished_at)}` : ""}
              </span>
            </Link>
            <Button busy={busy === task.id} onClick={() => void resume(`tasks/${task.id}/resume-interrupted`, task.id)}>
              Resume
            </Button>
          </li>
        ))}
      </ul>
      {error ? <Alert>{error}</Alert> : null}
    </section>
  );
}
