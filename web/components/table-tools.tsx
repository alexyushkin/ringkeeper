"use client";

import { useState } from "react";

import { SaveFile } from "@/components/save-file";
import { forExcel } from "@/lib/download";
import { toCsv, toTsv } from "@/lib/markdown-table";

/** Copy a table (to paste into a spreadsheet) or download it as CSV (in the desktop app,
 * saved into Downloads). */
export function TableTools({ head, rows }: { head: string[]; rows: string[][] }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="mb-1 flex flex-wrap justify-end gap-3 text-xs">
      <button
        type="button"
        className="text-muted hover:text-ink"
        onClick={() =>
          void navigator.clipboard.writeText(toTsv(head, rows)).then(() => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          })
        }
      >
        {copied ? "Copied" : "Copy"}
      </button>
      <SaveFile name="table.csv" text={() => forExcel(toCsv(head, rows))} type="text/csv" label="Download CSV" className="text-muted hover:text-ink" />
    </div>
  );
}
