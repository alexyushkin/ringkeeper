"use client";

import { useState } from "react";

import { Markdown } from "@/components/markdown";
import { SaveFile } from "@/components/save-file";
import { Alert, Button, Details } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { forExcel, reportName } from "@/lib/download";
import { mainTable, toCsv } from "@/lib/markdown-table";

type Kind = "setup-check" | "self-test";

const LINK = "text-sm text-accent hover:underline disabled:opacity-50";

/** Check my setup's or the Self-test's reports: the latest, with Save report, Copy report,
 * its table as CSV and Show in folder (desktop), and the earlier ones to open again. */
export function RunReports({ kind, title, latest, day }: { kind: Kind; title: string; latest: string; day: string }) {
  const listed = useApi<{ day: string; bytes: number }[]>(`run-reports/${kind}`, { latest: day });
  const [picked, setPicked] = useState<string | null>(null);
  const older = useApi<{ day: string; report: string }>(picked ? `run-reports/${kind}/${picked}` : null);
  const earlier = (listed.data ?? []).filter((r) => r.day !== day);
  return (
    <div className="space-y-3">
      <Details label={`Report from ${day}`}>
        <ReportTools kind={kind} title={title} day={day} text={latest} />
        <Markdown text={latest} className="text-sm" />
      </Details>
      {earlier.length ? (
        <Details label={`Earlier reports (${earlier.length})`}>
          <ul className="flex flex-wrap gap-2">
            {earlier.map((r) => (
              <li key={r.day}>
                <Button kind={picked === r.day ? "secondary" : "ghost"} onClick={() => setPicked(picked === r.day ? null : r.day)}>
                  {r.day}
                </Button>
              </li>
            ))}
          </ul>
          {older.error ? <Alert>{older.error}</Alert> : null}
          {picked && older.data?.day === picked ? (
            <div className="mt-3 space-y-2 border-t border-line pt-3">
              <ReportTools kind={kind} title={title} day={picked} text={older.data.report} />
              <Markdown text={older.data.report} className="text-sm" />
            </div>
          ) : null}
        </Details>
      ) : null}
    </div>
  );
}

function ReportTools({ kind, title, day, text }: { kind: Kind; title: string; day: string; text: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const table = mainTable(text);

  async function show() {
    setError(null);
    try {
      await api(`run-reports/${kind}/${day}/show`, { method: "POST" });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="mb-2 space-y-2">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <button
          type="button"
          className={LINK}
          onClick={() =>
            void navigator.clipboard.writeText(text).then(() => {
              setCopied(true);
              window.setTimeout(() => setCopied(false), 2000);
            })
          }
        >
          {copied ? "Copied" : "Copy report"}
        </button>
        {DESKTOP ? (
          <button type="button" className={LINK} title="Where Ringkeeper keeps its reports, one per day" onClick={() => void show()}>
            Show in folder
          </button>
        ) : null}
      </div>
      {/* Each saved copy says where it went, with its own Show in folder (Downloads). */}
      <div className="flex flex-col items-start gap-1">
        <SaveFile name={reportName(title, day, "md")} text={() => text} type="text/markdown" label="Save report" className={LINK} />
        {table ? <SaveFile name={reportName(title, day, "csv")} text={() => forExcel(toCsv(table.head, table.rows))} type="text/csv" label="Save table as CSV" className={LINK} /> : null}
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
