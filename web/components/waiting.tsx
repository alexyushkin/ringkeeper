"use client";

import { useEffect, useRef, useState } from "react";

import { DecisionCard, type Decision } from "@/components/decision-card";
import { inOrder, waitingLabel } from "@/lib/approvals";

/**
 * What's waiting for you on a task or conversation page (approvals, questions, plans), in
 * order, placed under the latest result where the reader is. One that appears while the
 * page is open is scrolled into view; while any is off-screen, a bar at the bottom says so
 * ("1 approval waiting") and jumps to it.
 */
export function Waiting({ decisions, onDone }: { decisions: Decision[]; onDone: () => void }) {
  const shown = inOrder(decisions);
  const cards = useRef(new Map<string, HTMLDivElement>());
  const seen = useRef<Set<string> | null>(null);
  const [hidden, setHidden] = useState<string[]>([]);
  const ids = shown.map((d) => d.id).join(",");

  // A new one while the page is open: bring it into view. Those there on arrival aren't.
  useEffect(() => {
    const now = ids ? ids.split(",") : [];
    if (seen.current === null) {
      seen.current = new Set(now);
      return;
    }
    const fresh = now.filter((id) => !seen.current!.has(id));
    now.forEach((id) => seen.current!.add(id));
    if (fresh.length) cards.current.get(fresh[0]!)?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [ids]);

  // Which are off-screen right now.
  useEffect(() => {
    if (typeof IntersectionObserver === "undefined") return;
    const off = new Set<string>();
    const watch = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        const id = (entry.target as HTMLElement).dataset.waiting ?? "";
        if (entry.isIntersecting) off.delete(id);
        else off.add(id);
      }
      setHidden([...off]);
    });
    cards.current.forEach((element) => watch.observe(element));
    return () => watch.disconnect();
  }, [ids]);

  if (!shown.length) return null;
  const away = shown.filter((d) => hidden.includes(d.id));
  return (
    <div data-waiting-list className="mt-4 space-y-3">
      {shown.map((d) => (
        <div
          key={d.id}
          data-waiting={d.id}
          ref={(element) => {
            if (element) cards.current.set(d.id, element);
            else cards.current.delete(d.id);
          }}
        >
          <DecisionCard decision={d} onDone={onDone} inline />
        </div>
      ))}
      {away.length ? (
        <div className="pointer-events-none fixed inset-x-0 bottom-20 z-30 flex justify-center px-4 md:bottom-4">
          <button
            type="button"
            data-waiting-bar
            className="pointer-events-auto rounded-full border border-warn bg-warn-soft px-4 py-2 text-sm font-medium text-warn shadow-lg"
            onClick={() => cards.current.get(away[0]!.id)?.scrollIntoView({ behavior: "smooth", block: "center" })}
          >
            {waitingLabel(away.map((d) => d.kind))}
          </button>
        </div>
      ) : null}
    </div>
  );
}
