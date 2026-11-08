"use client";

import Link from "@/components/link";
import { useState } from "react";

import { Alert, Button, clock } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { threadHref } from "@/lib/client/routes";

interface Scheduled {
  id: string;
  thread_id: string;
  body: string;
  run_at: string;
}

/** Stops a request sent for later before it runs. */
export function CancelButton({ id, onDone }: { id: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <>
      <Button
        kind="ghost"
        busy={busy}
        onClick={async () => {
          setBusy(true);
          setError(null);
          try {
            await api(`messages/${id}/cancel`, { method: "POST" });
            onDone();
          } catch (err) {
            setError(err instanceof Error ? err.message : String(err));
          } finally {
            setBusy(false);
          }
        }}
      >
        Cancel
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
}

/** Home: requests sent for later that haven't run yet, each with Cancel. */
export function ScheduledList() {
  const list = useApi<Scheduled[]>("scheduled");
  useLive((event) => {
    if (event.type === "message" || event.type === "message_routed") list.reload();
  });
  if (!list.data?.length) return null;
  return (
    <section aria-label="Sent for later" className="mt-6">
      <h2 className="mb-2 text-sm font-semibold text-muted">Sent for later</h2>
      <ul className="space-y-2">
        {list.data.map((one) => (
          <li key={one.id} className="flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-surface p-3">
            <span className="shrink-0 text-sm font-medium">{clock(one.run_at)}</span>
            <Link href={threadHref(one.thread_id)} className="min-w-0 flex-1 truncate text-sm hover:text-accent">
              {one.body.split("\n")[0]}
            </Link>
            <CancelButton id={one.id} onDone={list.reload} />
          </li>
        ))}
      </ul>
    </section>
  );
}
