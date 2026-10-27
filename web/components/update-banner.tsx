"use client";

import { useEffect, useState } from "react";

import { api, useApi } from "@/lib/client/api";

interface UpdateStatus {
  ready: { version: string; notes: string } | null;
  restart_requested: boolean;
  running: number;
}

const CHECK_EVERY_MS = 60_000;

/** Desktop app: an update is downloaded. It never restarts by itself: the
 * user picks the moment, and it still waits for running tasks to finish. */
export function UpdateBanner() {
  const update = useApi<UpdateStatus>("desktop/update");
  const [error, setError] = useState<string | null>(null);
  const { reload } = update;
  useEffect(() => {
    const timer = setInterval(reload, CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [reload]);

  const status = update.data;
  if (!status?.ready) return null;
  const restart = () =>
    api("desktop/update/restart", { method: "POST" })
      .then(() => {
        setError(null);
        reload();
      })
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));

  let text = `Update ready (${status.ready.version}). Restart when you're done.`;
  if (status.restart_requested) {
    text =
      status.running > 0
        ? `Restarting to update once ${status.running === 1 ? "the running task finishes" : `the ${status.running} running tasks finish`}.`
        : "Restarting to update…";
  }
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 bg-accent px-4 py-2 text-sm text-white" role="status">
      <span>{error ?? text}</span>
      {status.restart_requested ? null : (
        <button type="button" className="rounded-lg bg-white/20 px-3 py-1 font-medium" onClick={restart}>
          Restart now
        </button>
      )}
    </div>
  );
}
