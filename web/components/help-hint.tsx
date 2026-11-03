"use client";

import { useId, useState, type ReactNode } from "react";

import Link from "@/components/link";
import { docUrl } from "@/lib/client/docs";

/** A "?" beside a heading: one or two lines on what the page is for, and the guide's
 * page for more. Opens on click (or Enter); Escape or another click closes it. */
export function HelpHint({ doc, children }: { doc: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-block align-middle">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        aria-label="What's this?"
        onClick={() => setOpen(!open)}
        onKeyDown={(event) => {
          if (event.key === "Escape") setOpen(false);
        }}
        className="ml-2 grid size-6 place-items-center rounded-full border border-line text-xs font-semibold text-muted hover:border-accent hover:text-accent"
      >
        ?
      </button>
      {open ? (
        <span id={id} role="note" className="absolute top-8 left-0 z-30 block w-72 max-w-[80vw] rounded-xl border border-line bg-surface p-3 text-left text-sm font-normal text-ink shadow-lg">
          <span className="block">{children}</span>
          <span className="mt-2 flex flex-wrap gap-3">
            <a href={docUrl(doc)} target="_blank" rel="noreferrer" className="text-accent underline">
              Read more in the guide
            </a>
            <Link href="/help" className="text-accent underline">
              Ask about it
            </Link>
          </span>
        </span>
      ) : null}
    </span>
  );
}
