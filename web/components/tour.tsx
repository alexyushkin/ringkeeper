"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { Button } from "@/components/ui";
import { api } from "@/lib/client/api";

const START = "rk-tour";

const STEPS = [
  {
    target: "ask",
    title: "Ask here",
    text: "Say what you need in plain words, typed or spoken. Ringkeeper works out which client and project it's for, and starts a task.",
  },
  {
    target: "approvals",
    title: "Approvals",
    text: "Anything that would change live client data, or reach real people, waits here for your OK. Nothing runs until you approve it.",
  },
  {
    target: "connections",
    title: "Connectors",
    text: "The services each client's projects work with: databases, n8n, Airtable, GitHub, Google and more. Connect each one once per client, then link it to their projects. Ringkeeper keeps the keys; the AI never sees them.",
  },
] as const;

/** Show the tour now (Help, the command palette). */
export function startTour(): void {
  window.dispatchEvent(new Event(START));
}

function visible(name: string): HTMLElement | null {
  const all = Array.from(document.querySelectorAll<HTMLElement>(`[data-tour="${name}"]`));
  return all.find((el) => el.offsetParent !== null && el.getBoundingClientRect().width > 0) ?? null;
}

/** Three steps after the first run: the request box, Approvals, Connectors. Once
 * seen or skipped it doesn't come back by itself; Help can show it again. */
export function Tour({ auto, onSeen }: { auto: boolean; onSeen: () => void }) {
  const [step, setStep] = useState<number | null>(null);
  const [box, setBox] = useState<DOMRect | null>(null);
  // At most once while the app is open: `auto` turns on again each time Home opens.
  const shown = useRef(false);

  useEffect(() => {
    if (!auto || shown.current) return;
    shown.current = true;
    setStep(0);
  }, [auto]);
  useEffect(() => {
    const start = () => setStep(0);
    window.addEventListener(START, start);
    return () => window.removeEventListener(START, start);
  }, []);

  const measure = useCallback(() => {
    const target = step === null ? undefined : STEPS[step]?.target;
    if (!target) return;
    const el = visible(target);
    if (el) el.scrollIntoView({ block: "nearest" });
    setBox(el ? el.getBoundingClientRect() : null);
  }, [step]);

  useLayoutEffect(() => {
    measure();
    const later = window.setTimeout(measure, 400); // a page that was still opening
    window.addEventListener("resize", measure);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.removeEventListener("resize", measure);
      window.removeEventListener("scroll", measure, true);
      window.clearTimeout(later);
    };
  }, [measure]);

  const finish = useCallback(() => {
    setStep(null);
    void api("start/tour", { method: "POST" }).then(onSeen, () => undefined);
  }, [onSeen]);

  useEffect(() => {
    if (step === null) return;
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") finish();
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, [step, finish]);

  const current = step === null ? undefined : STEPS[step];
  if (step === null || !current) return null;
  const last = step === STEPS.length - 1;
  const pad = 6;
  // The card goes below the spotlight, or above it near the bottom of the window.
  const below = !box || box.bottom + 220 < window.innerHeight;
  const left = box ? Math.min(Math.max(box.left, 16), window.innerWidth - 336) : 16;
  const top = box ? (below ? box.bottom + pad + 10 : Math.max(box.top - pad - 10 - 200, 16)) : 80;

  return (
    <div className="fixed inset-0 z-50" role="dialog" aria-modal="true" aria-labelledby="tour-title">
      {box ? (
        <div
          aria-hidden
          className="pointer-events-none fixed rounded-xl ring-2 ring-accent"
          style={{ left: box.left - pad, top: box.top - pad, width: box.width + pad * 2, height: box.height + pad * 2, boxShadow: "0 0 0 9999px rgba(0,0,0,0.45)" }}
        />
      ) : (
        <div aria-hidden className="fixed inset-0 bg-black/45" />
      )}
      <div className="fixed w-80 max-w-[calc(100vw-32px)] space-y-2 rounded-2xl border border-line bg-surface p-4 shadow-xl" style={{ left, top }}>
        <p className="text-xs text-muted">
          {step + 1} of {STEPS.length}
        </p>
        <h2 id="tour-title" className="font-semibold">
          {current.title}
        </h2>
        <p className="text-sm">{current.text}</p>
        <div className="flex flex-wrap gap-2 pt-1">
          <Button kind="primary" onClick={() => (last ? finish() : setStep(step + 1))}>
            {last ? "Done" : "Next"}
          </Button>
          {step > 0 ? <Button onClick={() => setStep(step - 1)}>Back</Button> : null}
          {last ? null : (
            <Button kind="ghost" onClick={finish}>
              Skip the tour
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
