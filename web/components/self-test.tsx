"use client";

import { useEffect, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Alert, Button, ConfirmButton, Details, Section, Spinner } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

type Progress = { done: number; total: number; current: string | null; passed: number; failed: number; cost: number };
type Status = {
  running: boolean;
  scenarios: number;
  progress: Progress | null;
  failed: string | null;
  report: string | null;
  report_day: string | null;
};

// Run one and watch it: real requests through a scratch copy, with this install's Claude sign-in.
export function SelfTest() {
  const status = useApi<Status>("desktop/self-test");
  const [error, setError] = useState<string | null>(null);
  const running = status.data?.running ?? false;
  const reload = status.reload;

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [running, reload]);

  async function act(path: string) {
    setError(null);
    try {
      await api(path, { method: "POST" });
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const now = status.data?.progress;
  const day = status.data?.report_day;
  return (
    <Section id="self-test" title="Self-test" summary={running ? "running" : day ? `last run ${day}` : "not run yet"} defaultOpen={false}>
      <div className="space-y-3 text-sm">
        <p>
          Sends {status.data?.scenarios ?? "about 20"} real requests through Ringkeeper, against two made-up clients in a scratch folder. It checks where each one went, what was held, what was refused, and that changes can be undone.
        </p>
        <p className="text-muted">Your clients, connections and tasks aren&apos;t touched. It takes about 10 minutes and uses your Claude sign-in. On an API key that&apos;s usually well under a dollar.</p>
        {running ? (
          <div className="space-y-2">
            <p className="flex items-center gap-2">
              <Spinner />
              {now ? `${now.done} of ${now.total} done${now.current ? `: ${now.current}` : ""}` : "Getting ready..."}
            </p>
            {now && now.done ? (
              <p className="text-muted">
                {now.passed} passed, {now.failed} failed so far
              </p>
            ) : null}
            <Button onClick={() => act("desktop/self-test/stop")}>Stop after this one</Button>
          </div>
        ) : (
          <ConfirmButton question="Run the self-test? It takes about 10 minutes." confirm="Run it" onConfirm={() => act("desktop/self-test")}>
            Run the self-test
          </ConfirmButton>
        )}
        {status.data?.failed ? <Alert>The self-test stopped: {status.data.failed}</Alert> : null}
        {error ? <Alert>{error}</Alert> : null}
        {status.data?.report ? (
          <Details label={`Report from ${day}`}>
            <Markdown text={status.data.report} className="text-sm" />
          </Details>
        ) : null}
      </div>
    </Section>
  );
}
