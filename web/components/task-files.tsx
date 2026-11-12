"use client";

import { useState } from "react";

import { Alert, Button } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

interface TaskFile {
  path: string;
  size: number | null; // null: the task deleted it
  on_branch: boolean; // only on the task's branch until its changes are kept
  runs: boolean; // not a document (PDF, Office, text, CSV, JSON, a picture): shown in its folder, never opened
  can_open: boolean;
}

function sized(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/** The files a task made or changed, each with Open and Show in folder (desktop). */
export function TaskFiles({ id, compact = false }: { id: string; compact?: boolean }) {
  const files = useApi<TaskFile[]>(`tasks/${id}/files`);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (!files.data?.length) return null;

  async function open(path: string, reveal: boolean) {
    setBusy(`${path}:${reveal}`);
    setError(null);
    try {
      await api(`tasks/${id}/files/open`, { json: { path, reveal } });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  const onBranch = files.data.some((f) => f.on_branch);
  return (
    <div className={compact ? "mt-2" : "space-y-2"}>
      {compact ? <p className="text-xs font-medium text-muted">Files</p> : null}
      <ul className="space-y-1 text-sm">
        {files.data.map((file) => (
          <li key={file.path} className="flex flex-wrap items-center gap-2">
            <code className="min-w-0 break-all">{file.path}</code>
            <span className="text-xs text-muted">{file.size === null ? "deleted" : sized(file.size)}</span>
            {DESKTOP && file.can_open ? (
              <>
                {file.runs ? null : (
                  <Button kind="ghost" busy={busy === `${file.path}:false`} onClick={() => void open(file.path, false)}>
                    Open
                  </Button>
                )}
                <Button kind="ghost" busy={busy === `${file.path}:true`} onClick={() => void open(file.path, true)}>
                  Show in folder
                </Button>
              </>
            ) : null}
          </li>
        ))}
      </ul>
      {onBranch ? <p className="text-xs text-muted">These are on the task&apos;s branch: they open from a copy. Keep these changes to put them in the project&apos;s folder.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
