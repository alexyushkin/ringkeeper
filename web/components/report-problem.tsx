"use client";

import { useState } from "react";

import { Alert, Button, Card, Loading, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

const ISSUES = "https://github.com/alexyushkin/ringkeeper/issues/new";
// GitHub refuses very long addresses: the logs give way first (their end is kept).
const MAX_URL = 7500;

interface Report {
  about: Record<string, string>;
  logs: Record<string, string>;
  text: string;
}

/** The issue page, prefilled: the bug form's fields by their ids (.github/ISSUE_TEMPLATE/bug.yml). */
export function issueUrl(what: string, report: Report, text: string): string {
  const base = new URLSearchParams({
    template: "bug.yml",
    title: what.split("\n")[0]?.slice(0, 80) || "Something isn't working",
    what,
    version: report.about.version ?? "",
    install: report.about.install ?? "",
  });
  const room = MAX_URL - `${ISSUES}?${base}&logs=`.length;
  let logs = text;
  while (logs && encodeURIComponent(logs).length > room) logs = logs.slice(Math.ceil(logs.length / 10));
  if (logs !== text) logs = `(the start is cut to fit; the whole report can be saved as a zip)\n${logs}`;
  base.set("logs", logs);
  return `${ISSUES}?${base}`;
}

/** Help › Report a problem: what goes in the report, shown and editable before anything leaves. */
export function ReportProblem({ onClose }: { onClose: () => void }) {
  const report = useApi<Report>("report");
  const [what, setWhat] = useState("");
  const [text, setText] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  if (report.error) return <Alert>{report.error}</Alert>;
  if (!report.data) return <Loading />;
  const shown = text ?? report.data.text;

  async function save() {
    setError(null);
    try {
      if (DESKTOP) {
        const done = await api<{ path: string }>("report/zip", { json: { text: shown, what } });
        setSaved(done.path);
      } else {
        const response = await fetch("/bff/report/zip", {
          method: "POST",
          headers: { "Content-Type": "application/json", "x-csrf-token": decodeURIComponent(document.cookie.match(/(?:^|;\s*)rk_csrf=([^;]+)/)?.[1] ?? "") },
          body: JSON.stringify({ text: shown, what }),
          credentials: "same-origin",
        });
        if (!response.ok) throw new Error("Couldn't make the zip. Try again.");
        const link = document.createElement("a");
        link.href = URL.createObjectURL(await response.blob());
        link.download = "Ringkeeper-report.zip";
        link.click();
        setSaved("your downloads");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Card className="space-y-3">
      <h2 className="font-semibold">Report a problem</h2>
      <TextArea label="What happened?" value={what} onChange={setWhat} rows={3} hint="What you did, what you expected, and what you saw instead." />
      <TextArea
        label="What's included (you can edit it)"
        value={shown}
        onChange={setText}
        rows={12}
        hint="The version, your system, a few counts, and the end of Ringkeeper's logs. Keys, tokens, passwords and email addresses are removed; names of clients and projects may still appear in the logs, so read it and remove anything you'd rather not share."
      />
      <p className="text-xs text-muted">Nothing is sent by Ringkeeper. Open the GitHub issue to send it yourself (a GitHub account is needed), or save it as a zip to attach or email.</p>
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" disabled={!what.trim()} onClick={() => window.open(issueUrl(what, report.data!, shown), "_blank", "noopener")}>
          Open a GitHub issue
        </Button>
        <Button onClick={save}>Save as a zip</Button>
        <Button kind="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      {saved ? <Alert tone="good">Saved to {saved}.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}
