"use client";

import { useState } from "react";

import { api } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { fileName, saveVia } from "@/lib/download";

/** Save a file the page made: a download link in a browser; in the desktop app (whose
 * window ignores those) saved into Downloads, then "Saved" with Show in folder. */
export function SaveFile({ name, text, type, label, className }: { name: string; text: () => string; type: string; label: string; className: string }) {
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!DESKTOP) {
    const link = saveVia(false, name, text(), type);
    return link.how === "link" ? (
      <a href={link.href} download={link.name} className={className}>
        {label}
      </a>
    ) : null;
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const done = await api<{ path: string }>("desktop/downloads", { json: { name, text: text() } });
      setSaved(done.path);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function show(path: string) {
    setError(null);
    try {
      await api("desktop/downloads/show", { json: { path } });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <button type="button" className={className} disabled={busy} onClick={() => void save()}>
        {label}
      </button>
      {saved ? (
        <>
          <span className="text-muted" title={saved}>
            Saved as {fileName(saved)} in Downloads
          </span>
          <button type="button" className={className} onClick={() => void show(saved)}>
            Show in folder
          </button>
        </>
      ) : null}
      {error ? <span className="text-bad">{error}</span> : null}
    </span>
  );
}
