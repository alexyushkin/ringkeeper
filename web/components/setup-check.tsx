"use client";

import { useEffect, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Alert, Button, ConfirmButton, Details, Section, Spinner } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

type Progress = { done: number; total: number; current: string | null };
type Status = {
  running: boolean;
  progress: Progress | null;
  failed: string | null;
  requests: number;
  gaps: number;
  conversations: number;
  report: string | null;
  report_day: string | null;
};

// Real read-only requests on this install's own projects and connections, through the real
// router, worker and gateway. Its conversations are marked, and archived together.
export function SetupCheck() {
  const status = useApi<Status>("setup-check");
  const [error, setError] = useState<string | null>(null);
  const [archived, setArchived] = useState<number | null>(null);
  const running = status.data?.running ?? false;
  const reload = status.reload;

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [running, reload]);

  async function act(path: string) {
    setError(null);
    setArchived(null);
    try {
      const done = await api<{ archived?: number }>(path, { method: "POST" });
      if (typeof done?.archived === "number") setArchived(done.archived);
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const now = status.data?.progress;
  const day = status.data?.report_day;
  const requests = status.data?.requests ?? 0;
  const kept = status.data?.conversations ?? 0;
  return (
    <Section id="setup-check" title="Check my setup" summary={running ? "running" : day ? `last run ${day}` : "not run yet"} defaultOpen={false}>
      <div className="space-y-3 text-sm">
        <p>
          Sends {requests ? `${requests} real requests` : "real requests"} through Ringkeeper, on your own projects: one per connection linked to each, then a question about your setup, a web lookup and a follow-up. The report says, for each one, where it went, whether it asked anything, what ran, what came back, and what it cost.
        </p>
        <p className="text-muted">
          It only reads. Its tasks can&apos;t edit files or run commands, and anything that would change something is held for you in Approvals, not done.
          {status.data?.gaps ? ` ${status.data.gaps === 1 ? "One connection is" : `${status.data.gaps} connections are`} linked to no project: the report lists ${status.data.gaps === 1 ? "it" : "them"}, without sending anything.` : ""}
        </p>
        {running ? (
          <div className="space-y-2">
            <p className="flex items-center gap-2">
              <Spinner />
              {now && now.total ? `${now.done} of ${now.total} done${now.current ? `: ${now.current}` : ""}` : "Getting ready..."}
            </p>
            <Button onClick={() => act("setup-check/stop")}>Stop after this one</Button>
          </div>
        ) : (
          <ConfirmButton question={`Send ${requests || "these"} read-only requests through your setup? Each one uses your Claude sign-in.`} confirm="Check it" kind="secondary" onConfirm={() => act("setup-check")}>
            Check my setup
          </ConfirmButton>
        )}
        {status.data?.failed ? <Alert>Check my setup stopped: {status.data.failed}</Alert> : null}
        {error ? <Alert>{error}</Alert> : null}
        {!running && kept ? (
          <p className="flex flex-wrap items-center gap-2">
            <span className="text-muted">
              {kept} conversation{kept === 1 ? "" : "s"} from Check my setup.
            </span>
            <Button kind="ghost" onClick={() => act("setup-check/archive")}>
              Archive {kept === 1 ? "it" : "them"}
            </Button>
          </p>
        ) : null}
        {archived !== null ? <p className="text-muted">Archived {archived} conversation{archived === 1 ? "" : "s"}. They stay searchable.</p> : null}
        {status.data?.report ? (
          <Details label={`Report from ${day}`}>
            <Markdown text={status.data.report} className="text-sm" />
          </Details>
        ) : null}
      </div>
    </Section>
  );
}
