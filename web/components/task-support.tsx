"use client";

import { useState } from "react";

import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/client/api";

/** How a task ran, copied for support: its sessions and why each started, how each ended, its
 * held calls and the app's version. Never its prompt, result, project or client
 * (services/task_support). */
export function TaskSupport({ id }: { id: string }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function copy() {
    setBusy(true);
    setSaid(null);
    setError(null);
    try {
      const found = await api<{ text: string }>(`tasks/${encodeURIComponent(id)}/support`);
      await navigator.clipboard.writeText(found.text);
      setSaid("Copied the details for support. Paste them where you need them.");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-6 space-y-1">
      <Button kind="ghost" busy={busy} onClick={() => void copy()}>
        Copy details for support
      </Button>
      <p className="text-xs text-muted">How it ran: its sessions, why each started and how each ended. No request, result or client data.</p>
      {said ? <Alert tone="good">{said}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
