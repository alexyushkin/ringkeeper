"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, Button } from "@/components/ui";
import { api } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { dataUrl, fileName } from "@/lib/download";

/** What a card can do for you to pass on: run from its buttons, or from Ctrl+K. */
export type ExportAction = "copy-tools" | "save-tools" | "support";
export const EXPORT_ACTIONS: ExportAction[] = ["copy-tools", "save-tools", "support"];
/** One request to run an action; `n` tells a second request for the same one apart. */
export interface ExportStart {
  action: ExportAction;
  n: number;
}
const ASK = "rk-connection-export";

/** Ask a card that's on screen to run an action: true if one took it. */
export function askCard(name: string, action: ExportAction): boolean {
  if (typeof document === "undefined" || !document.getElementById(`connection-${name}`)) return false;
  window.dispatchEvent(new CustomEvent(ASK, { detail: { name, action } }));
  return true;
}

/** A card's side of askCard: `onAsk` gets the actions asked of it. */
export function useAsked(name: string, onAsk: (action: ExportAction) => void): void {
  const latest = useRef(onAsk);
  latest.current = onAsk;
  useEffect(() => {
    const heard = (event: Event) => {
      const detail = (event as CustomEvent<{ name: string; action: ExportAction }>).detail;
      if (detail.name === name) latest.current(detail.action);
    };
    window.addEventListener(ASK, heard);
    return () => window.removeEventListener(ASK, heard);
  }, [name]);
}

export const EXPORT_LABELS: Record<ExportAction, string> = {
  "copy-tools": "Copy tool list",
  "save-tools": "Save tool list",
  support: "Copy details for support",
};

interface ToolList {
  count: number;
  tools: unknown[];
}

/** The tool list's file: the connection's own name (its slug), so it's always a safe one. */
export function toolListFile(name: string): string {
  return `${name} tools.json`;
}

/** A connection's tool list and its details for support, to copy or save. Neither holds a
 * token, a key or a client's data (services/connection_export). `start` runs one right away:
 * Ctrl+K sends you here with it. */
export function ConnectionExports({ name, start }: { name: string; start?: ExportStart | null }) {
  const [busy, setBusy] = useState<ExportAction | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const started = useRef(0);

  async function run(action: ExportAction) {
    setBusy(action);
    setError(null);
    setSaid(null);
    setSaved(null);
    try {
      if (action === "support") {
        const found = await api<{ text: string }>(`connections/${encodeURIComponent(name)}/support`);
        await navigator.clipboard.writeText(found.text);
        setSaid("Copied the details for support. Paste them where you need them.");
        return;
      }
      const list = await api<ToolList>(`connections/${encodeURIComponent(name)}/tool-list`);
      const text = `${JSON.stringify(list, null, 2)}\n`;
      const tools = `${list.count} tool${list.count === 1 ? "" : "s"}`;
      if (action === "copy-tools") {
        await navigator.clipboard.writeText(text);
        setSaid(`Copied the list of ${tools}.`);
      } else if (DESKTOP) {
        const done = await api<{ path: string }>("desktop/downloads", { json: { name: toolListFile(name), text } });
        setSaved(done.path);
        setSaid(`Saved the list of ${tools} as ${fileName(done.path)} in Downloads.`);
      } else {
        const link = document.createElement("a");
        link.href = dataUrl(text, "application/json");
        link.download = toolListFile(name);
        link.click();
        setSaid(`Saved the list of ${tools} to your downloads.`);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  useEffect(() => {
    if (start && started.current !== start.n) {
      started.current = start.n;
      void run(start.action);
    }
  }, [start]);

  async function show(path: string) {
    setError(null);
    try {
      await api("desktop/downloads/show", { json: { path } });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-1">
      <div className="flex flex-wrap gap-2">
        {EXPORT_ACTIONS.map((action) => (
          <Button key={action} kind="ghost" busy={busy === action} disabled={busy !== null} onClick={() => void run(action)}>
            {EXPORT_LABELS[action]}
          </Button>
        ))}
        {saved ? (
          <Button kind="ghost" onClick={() => void show(saved)}>
            Show in folder
          </Button>
        ) : null}
      </div>
      <p className="text-xs text-muted">No tokens, keys or client data: tool names, the rules, argument names and how it connects.</p>
      {said ? <Alert tone="good">{said}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
