"use client";

import { useEffect, useState, type ReactNode } from "react";

import { Alert, Button, Toggle, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface UpdateStatus {
  version: string | null; // this one
  ready: { version: string; notes: string } | null;
  restart_requested: boolean;
  running: number;
  checking: boolean;
  downloading: string | null; // the version being downloaded
  unanswered: boolean; // asked, and the app never started checking
  last: { at: string; result: "current" | "ready" | "failed"; error: string | null } | null;
  check_updates: boolean;
}

const CHECK_EVERY_MS = 60_000;
const WHILE_CHECKING_MS = 1_000;

function useUpdate() {
  const update = useApi<UpdateStatus>("desktop/update");
  const { reload } = update;
  const busy = update.data?.checking || update.data?.restart_requested;
  useEffect(() => {
    const timer = setInterval(reload, busy ? WHILE_CHECKING_MS : CHECK_EVERY_MS);
    return () => clearInterval(timer);
  }, [reload, busy]);
  return update;
}

/** "Install and restart", or, while tasks run, a restart that waits for them. */
function restartLabel(running: number): string {
  return running > 0 ? "Restart when tasks finish" : "Install and restart";
}

function restarting(running: number): string {
  if (running === 0) return "Restarting to update…";
  return `Restarting to update once ${running === 1 ? "the running task finishes" : `the ${running} running tasks finish`}.`;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Release notes are the CHANGELOG's Markdown: shown as plain text. */
function plain(notes: string): string {
  return notes.replace(/\*\*(.+?)\*\*/g, "$1").replace(/`([^`]+)`/g, "$1");
}

/** Desktop app: an update is downloaded. It never restarts by itself: the
 * user picks the moment, and it still waits for running tasks to finish. */
export function UpdateBanner() {
  const update = useUpdate();
  const [error, setError] = useState<string | null>(null);
  const status = update.data;
  if (!status?.ready) return null;
  const restart = () =>
    api("desktop/update/restart", { method: "POST" })
      .then(() => {
        setError(null);
        update.reload();
      })
      .catch((err: unknown) => setError(errorText(err)));

  const text = status.restart_requested ? restarting(status.running) : `Update ready (${status.ready.version}).`;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 bg-accent px-4 py-2 text-sm text-white" role="status">
      <span>{error ?? text}</span>
      {status.restart_requested ? null : (
        <button type="button" className="rounded-lg bg-white/20 px-3 py-1 font-medium" onClick={restart}>
          {restartLabel(status.running)}
        </button>
      )}
    </div>
  );
}

/** Settings › About: this version, the last check and its result, Check for updates,
 * and "Check automatically". */
export function UpdatesCard() {
  const update = useUpdate();
  const [error, setError] = useState<string | null>(null);
  const status = update.data;
  if (!status) return null;

  const run = (request: Promise<unknown>) =>
    request
      .then(() => {
        setError(null);
        update.reload();
      })
      .catch((err: unknown) => setError(errorText(err)));
  const check = () => run(api("desktop/update/check", { method: "POST" }));
  const restart = () => run(api("desktop/update/restart", { method: "POST" }));
  const automatic = (on: boolean) => run(api("settings", { method: "PATCH", json: { check_updates: on } }));

  const last = status.last;
  let result: ReactNode = null;
  if (status.unanswered) {
    result = <Alert>The app didn&apos;t start the check. Quit Ringkeeper from the tray, open it again, and try again.</Alert>;
  } else if (status.checking) {
    result = <p className="text-sm">{status.downloading ? `Downloading ${status.downloading}…` : "Checking for updates…"}</p>;
  } else if (status.ready) {
    result = (
      <div className="space-y-2">
        <p className="text-sm font-medium">Update available: {status.ready.version}</p>
        {status.ready.notes ? (
          <p className="max-h-60 overflow-y-auto rounded-xl bg-sunken p-3 text-sm whitespace-pre-line">{plain(status.ready.notes)}</p>
        ) : null}
        {status.restart_requested ? (
          <p className="text-sm">{restarting(status.running)}</p>
        ) : (
          <>
            <Button kind="primary" onClick={restart}>
              {restartLabel(status.running)}
            </Button>
            {status.running > 0 ? (
              <p className="text-xs text-muted">
                {status.running === 1 ? "A task is running" : `${status.running} tasks are running`}; Ringkeeper never restarts in the middle of one.
              </p>
            ) : null}
          </>
        )}
      </div>
    );
  } else if (last?.result === "failed") {
    result = <Alert>{last.error ?? "The update check failed."}</Alert>;
  } else if (last?.result === "current") {
    result = <Alert tone="good">You&apos;re up to date.</Alert>;
  }

  return (
    <div className="space-y-3 border-t border-line pt-3">
      <h3 className="font-semibold">Updates</h3>
      <p className="text-sm">
        This is version {status.version ?? "unknown"}.{" "}
        <span className="text-muted">{last ? `Last checked ${timeAgo(last.at)}.` : "Not checked yet."}</span>
      </p>
      {result}
      <Button busy={status.checking && !status.unanswered} disabled={status.checking} onClick={check}>
        Check for updates
      </Button>
      <Toggle
        label="Check automatically"
        checked={status.check_updates}
        onChange={automatic}
        hint="Every 6 hours, while no task is running. An update is downloaded and waits for you to install it."
      />
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
