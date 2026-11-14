"use client";

import type { MouseEvent, ReactNode } from "react";

import Link from "@/components/link";
import { appHistory, usePlace } from "@/lib/client/app-history";
import { DESKTOP } from "@/lib/client/routes";

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

function Arrow({ label, keys, d, enabled, onClick }: { label: string; keys: string; d: string; enabled: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={!enabled}
      aria-label={label}
      title={`${label} (${keys})`}
      className="grid size-8 place-items-center rounded-lg text-ink hover:bg-sunken disabled:text-muted disabled:opacity-40 disabled:hover:bg-transparent"
    >
      <svg viewBox="0 0 24 24" className="size-4" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
        <path d={d} />
      </svg>
    </button>
  );
}

/** Back and Forward, at the window's top left, in the desktop app only: a browser has its own.
 * Greyed out when there's nowhere to go. */
export function HistoryArrows() {
  const place = usePlace();
  if (!DESKTOP) return null;
  return (
    <div data-history-arrows className="fixed top-2 left-2 z-40 flex gap-0.5 rounded-xl bg-surface/90 p-0.5" role="group" aria-label="History">
      <Arrow label="Back" keys={MAC ? "⌘[" : "Alt+Left"} d="M15 18l-6-6 6-6" enabled={place.index > 0} onClick={() => appHistory()?.back()} />
      <Arrow label="Forward" keys={MAC ? "⌘]" : "Alt+Right"} d="M9 18l6-6-6-6" enabled={place.index < place.furthest} onClick={() => appHistory()?.forward()} />
    </div>
  );
}

/** A page's "‹ Back" link: back in history when you came from inside the app, and to `href`
 * (the page's parent) when you opened this page directly. */
export function BackLink({ href, children }: { href: string; children: ReactNode }) {
  const place = usePlace();
  function back(event: MouseEvent<HTMLAnchorElement>) {
    if (place.index === 0 || event.metaKey || event.ctrlKey || event.shiftKey) return; // the parent
    event.preventDefault();
    appHistory()?.back();
  }
  return (
    <Link href={href} onClick={back} className="text-sm text-accent">
      {children}
    </Link>
  );
}
