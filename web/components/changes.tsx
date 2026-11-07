"use client";

import { useConnectionTitles } from "@/components/connection-form";
import Link from "@/components/link";
import { useId, useState } from "react";

import { Alert, Badge, Button, timeAgo } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";

interface Change {
  id: string;
  at: string;
  connection: string;
  what: string;
  expires: string;
  restores: string | null;
  why_not: string | null;
  can_revert: boolean;
  reverting: boolean;
  reverted_at: string | null;
  expired: boolean;
}

/** What a task (or a conversation's tasks) changed on connections: Revert for 7 days,
 * where it can be undone, or why it can't. Reverting waits for approval too. */
export function Changes({ task, thread, path, heading = "Data it changed" }: { task?: string; thread?: string; path?: string; heading?: string }) {
  const list = useApi<Change[]>(path ?? (task ? `tasks/${task}/changes` : `threads/${thread}/changes`));
  const titleId = useId(); // a page can show several lists
  useLive((event) => {
    if (["approval", "task"].includes(event.type)) list.reload();
  });
  if (!list.data?.length) return null;
  return (
    <section className="mt-4 space-y-2" aria-labelledby={titleId}>
      <h2 id={titleId} className="text-sm font-semibold text-muted">
        {heading}
      </h2>
      <ul className="space-y-2">
        {list.data.map((change) => (
          <ChangeRow key={change.id} change={change} onDone={list.reload} />
        ))}
      </ul>
    </section>
  );
}

function ChangeRow({ change, onDone }: { change: Change; onDone: () => void }) {
  const title = useConnectionTitles();
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [asked, setAsked] = useState(false);

  async function revert() {
    setBusy(true);
    setError(null);
    try {
      await api(`changes/${change.id}/revert`, { method: "POST" });
      setAsked(true);
      setAsking(false);
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <li className="space-y-2 rounded-2xl border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="min-w-0 flex-1 break-words font-mono text-xs">{change.what}</span>
        <span className="text-xs text-muted">
          on {title(change.connection)} · {timeAgo(change.at)}
        </span>
      </div>
      {change.reverted_at ? (
        <Badge tone="good">Reverted {timeAgo(change.reverted_at)}</Badge>
      ) : change.reverting || asked ? (
        <p className="text-sm">
          Reverting waits for your OK in{" "}
          <Link href="/approvals" className="text-accent underline">
            Approvals
          </Link>
          .
        </p>
      ) : change.can_revert ? (
        asking ? (
          <div role="alertdialog" aria-label="Revert this change" className="space-y-2 rounded-xl border border-warn p-3">
            <p className="text-sm">{change.restores}</p>
            <p className="text-xs text-muted">This asks for approval first, like any change. If something changed it since, you&apos;ll see that before you approve.</p>
            <div className="flex flex-wrap gap-2">
              <Button kind="primary" busy={busy} onClick={revert}>
                Ask to revert
              </Button>
              <Button kind="ghost" onClick={() => setAsking(false)}>
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={() => setAsking(true)}>Revert</Button>
            <span className="text-xs text-muted">Possible until {new Date(change.expires).toLocaleDateString()}.</span>
          </div>
        )
      ) : (
        <p className="text-xs text-muted">{change.expired ? "Kept for 7 days; this one is older, so it can't be reverted any more." : change.why_not}</p>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </li>
  );
}
