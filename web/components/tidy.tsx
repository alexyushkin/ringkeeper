"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Field, Toggle } from "@/components/ui";
import { ApiError, api } from "@/lib/client/api";

/** Archiving and deleting tasks and conversations: one at a time or many at once. */

export type Kind = "task" | "thread";
const PLURAL: Record<Kind, [string, string]> = { task: ["task", "tasks"], thread: ["conversation", "conversations"] };
const ACTIVE = new Set(["running", "queued"]);

function noun(kind: Kind, count: number): string {
  return count === 1 ? PLURAL[kind][0] : `${count} ${PLURAL[kind][1]}`;
}

export function useSelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return {
    selected,
    has: (id: string) => selected.has(id),
    toggle: (id: string) =>
      setSelected((all) => {
        const next = new Set(all);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        return next;
      }),
    setAll: (ids: string[]) => setSelected(new Set(ids)),
    clear: () => setSelected(new Set()),
  };
}

/** Search (which also finds archived ones) and the archived view. */
export function ListTools({ query, onQuery, archived, onArchived, what }: { query: string; onQuery: (v: string) => void; archived: boolean; onArchived: (v: boolean) => void; what: string }) {
  return (
    <div className="mb-3 grid gap-2 sm:grid-cols-[1fr_auto] sm:items-end">
      <Field label={`Search ${what}`} value={query} onChange={onQuery} placeholder="Words from the request or result" hint={query ? "Archived ones are included." : undefined} />
      <Toggle label="Archived" checked={archived} onChange={onArchived} />
    </div>
  );
}

export function SelectBox({ checked, onChange, label }: { checked: boolean; onChange: () => void; label: string }) {
  return (
    <input
      type="checkbox"
      aria-label={label}
      className="size-4 shrink-0 accent-[var(--color-accent)]"
      checked={checked}
      onChange={onChange}
      onClick={(event) => event.stopPropagation()}
    />
  );
}

export function BulkBar({ kind, ids, archivedView, onDone, onClear }: { kind: Kind; ids: string[]; archivedView: boolean; onDone: () => void; onClear: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!ids.length) return null;

  async function archive(archived: boolean) {
    setError(null);
    try {
      await api(`${kind}s/archive`, { json: { ids, archived } });
      onClear();
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="sticky top-0 z-10 mb-3 space-y-2 rounded-2xl border border-line bg-surface p-3 shadow-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{noun(kind, ids.length)} selected</span>
        <span className="ml-auto" />
        <Button onClick={() => archive(!archivedView)}>{archivedView ? "Unarchive" : "Archive"}</Button>
        <Button kind="danger" onClick={() => setDeleting(true)}>
          Delete
        </Button>
        <Button kind="ghost" onClick={onClear}>
          Clear
        </Button>
      </div>
      {error ? <Alert>{error}</Alert> : null}
      {deleting ? (
        <DeleteDialog
          kind={kind}
          ids={ids}
          onClose={() => setDeleting(false)}
          onDone={() => {
            setDeleting(false);
            onClear();
            onDone();
          }}
        />
      ) : null}
    </div>
  );
}

interface Branch {
  name: string;
  exists: boolean;
  unmerged: number | null;
}

/**
 * Confirms a delete. One task: offers its git branch (ticked unless it has unmerged
 * work). Several, or conversations: their branches, keeping any with unmerged work.
 * A running task can't be deleted: it offers Stop and delete instead.
 */
export function DeleteDialog({ kind, ids, onClose, onDone, status }: { kind: Kind; ids: string[]; onClose: () => void; onDone: () => void; status?: string }) {
  const single = kind === "task" && ids.length === 1 ? ids[0] : null;
  const [branch, setBranch] = useState<Branch | null | undefined>(single ? undefined : null);
  const [branches, setBranches] = useState(!single);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(status ? ACTIVE.has(status) : false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!single) return;
    api<{ branch: Branch | null }>(`tasks/${single}/branch`)
      .then((answer) => {
        setBranch(answer.branch);
        setBranches(Boolean(answer.branch?.exists) && answer.branch?.unmerged === 0);
      })
      .catch(() => setBranch(null));
  }, [single]);

  async function remove() {
    setBusy(true);
    setError(null);
    try {
      await api(`${kind}s/delete`, { json: { ids, delete_branches: branches, unmerged_too: Boolean(single && branches) } });
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409 && single) setRunning(true);
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function stopAndDelete() {
    if (!single) return;
    setBusy(true);
    setError(null);
    try {
      await api(`tasks/${single}/stop`, { method: "POST" });
      const deadline = Date.now() + 90_000;
      for (;;) {
        const task = await api<{ status: string }>(`tasks/${single}`);
        if (!ACTIVE.has(task.status)) break;
        if (Date.now() > deadline) throw new Error("It's still stopping. Try deleting again in a minute.");
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
      await api("tasks/delete", { json: { ids, delete_branches: branches, unmerged_too: branches } });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const what = noun(kind, ids.length);
  return (
    <div role="dialog" aria-modal="true" className="fixed inset-0 z-30 flex items-center justify-center bg-black/40 p-4" onClick={onClose}>
      <div className="w-full max-w-md space-y-3 rounded-2xl bg-surface p-5 shadow-lg" onClick={(event) => event.stopPropagation()}>
        <h2 className="text-lg font-semibold">Delete {what}?</h2>
        <p className="text-sm text-muted">
          {kind === "thread" ? "Its messages and its tasks go too. " : ""}This can&apos;t be undone. The activity log keeps its record of what ran.
        </p>
        {single && branch === undefined ? <p className="text-sm text-muted">Checking the task&apos;s branch…</p> : null}
        {single && branch?.exists ? (
          <Toggle
            label={`Also delete its branch ${branch.name}`}
            checked={branches}
            onChange={setBranches}
            hint={branch.unmerged ? `It has ${branch.unmerged} commit${branch.unmerged === 1 ? "" : "s"} that aren't merged.` : "Its work is merged (or it has none)."}
          />
        ) : null}
        {!single ? <Toggle label="Also delete their branches" checked={branches} onChange={setBranches} hint="Branches with work that isn't merged are kept." /> : null}
        {error ? <Alert>{error}</Alert> : null}
        <div className="flex flex-wrap gap-2">
          {running && single ? (
            <Button kind="danger" busy={busy} onClick={stopAndDelete}>
              Stop and delete
            </Button>
          ) : (
            <Button kind="danger" busy={busy} onClick={remove}>
              Delete
            </Button>
          )}
          <Button kind="ghost" onClick={onClose}>
            Cancel
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Archive and Delete for one task or conversation (its own page). */
export function TidyButtons({ kind, id, archived, status, onArchived, onDeleted }: { kind: Kind; id: string; archived: boolean; status?: string; onArchived: () => void; onDeleted: () => void }) {
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function archive() {
    setError(null);
    try {
      await api(`${kind}s/archive`, { json: { ids: [id], archived: !archived } });
      onArchived();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="flex flex-wrap items-center gap-2">
      <Button kind="ghost" onClick={archive}>
        {archived ? "Unarchive" : "Archive"}
      </Button>
      <Button kind="ghost" onClick={() => setDeleting(true)}>
        Delete
      </Button>
      {error ? <Alert>{error}</Alert> : null}
      {deleting ? <DeleteDialog kind={kind} ids={[id]} status={status} onClose={() => setDeleting(false)} onDone={onDeleted} /> : null}
    </div>
  );
}
