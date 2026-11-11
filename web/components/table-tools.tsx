"use client";

import { useState } from "react";

import { toCsv, toTsv } from "@/lib/markdown-table";

/** Copy a table (to paste into a spreadsheet) or download it as CSV. */
export function TableTools({ head, rows }: { head: string[]; rows: string[][] }) {
  const [copied, setCopied] = useState(false);
  const csv = `data:text/csv;charset=utf-8,${encodeURIComponent("\uFEFF" + toCsv(head, rows))}`;
  return (
    <div className="mb-1 flex justify-end gap-3 text-xs">
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
      <a href={csv} download="table.csv" className="text-muted hover:text-ink">
        Download CSV
      </a>
    </div>
  );
}
