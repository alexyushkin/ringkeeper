"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type RefObject } from "react";

import { createHistory, type AppHistory, type Place } from "./history";
import { DESKTOP } from "./routes";

// The window's one AppHistory (lib/client/history.ts), made on first use in the browser.
let made: AppHistory | null = null;

export function appHistory(): AppHistory | null {
  if (typeof window === "undefined") return null;
  if (made === null) {
    made = createHistory({
      history: window.history,
      sessionStorage: window.sessionStorage,
      addEventListener: (type, listener) => window.addEventListener(type, listener as EventListener),
      get scrollY() {
        return window.scrollY;
      },
      scrollTo: (x, y) => window.scrollTo(x, y),
      scrollable: () => document.documentElement.scrollHeight - window.innerHeight,
      later: (run, ms) => window.setTimeout(run, ms),
    });
    if (DESKTOP) listenForBackAndForward(made);
  }
  return made;
}

const NOWHERE: Place = { index: 0, furthest: 0 };

/** Where this tab is in the app's history: Back is possible when index > 0, Forward when
 * index < furthest. */
export function usePlace(): Place {
  const subscribe = (changed: () => void) => appHistory()?.subscribe(changed) ?? (() => {});
  const snapshot = useRef(NOWHERE);
  const read = () => {
    const now = appHistory()?.place() ?? NOWHERE;
    if (now.index !== snapshot.current.index || now.furthest !== snapshot.current.furthest) snapshot.current = now;
    return snapshot.current;
  };
  return useSyncExternalStore(subscribe, read, () => NOWHERE);
}

/** While `open`, Back closes this dialog (calls `close`) instead of leaving the page. */
export function useBackCloses(open: boolean, close: () => void): void {
  const latest = useRef(close);
  latest.current = close;
  useEffect(() => {
    if (!open) return;
    return appHistory()?.holdBack(() => latest.current());
  }, [open]);
}

/** A folded section that opens as it was on this page in history: Back to a page shows the
 * same details open. `name` is its label; which of the page's sections with that label it is
 * comes from where it is on the page (`ref`), the same each time the page is shown. */
export function useOpenHere(name: string, initial: boolean): [boolean, (open: boolean) => void, RefObject<HTMLDivElement | null>] {
  const ref = useRef<HTMLDivElement | null>(null);
  const [open, setOpen] = useState(initial);
  const key = () => {
    const same = [...document.querySelectorAll<HTMLElement>("[data-details]")].filter((e) => e.dataset.details === name);
    return `${name}#${Math.max(0, same.indexOf(ref.current as HTMLElement))}`;
  };
  useEffect(() => {
    const was = appHistory()?.wasOpen(key());
    if (was !== null && was !== undefined) setOpen(was);
  }, [name]);
  return [
    open,
    (next: boolean) => {
      setOpen(next);
      appHistory()?.setOpen(key(), next);
    },
    ref,
  ];
}

const MAC = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/** Alt+Left and Alt+Right (Cmd+[ and Cmd+] on a Mac), and a mouse's back and forward
 * buttons. The window's own handling is kept from them: a dialog that's open closes first. */
function listenForBackAndForward(history: AppHistory): void {
  window.addEventListener(
    "keydown",
    (event) => {
      const back = MAC ? event.metaKey && event.key === "[" : event.altKey && event.key === "ArrowLeft";
      const forward = MAC ? event.metaKey && event.key === "]" : event.altKey && event.key === "ArrowRight";
      if (!back && !forward) return;
      event.preventDefault();
      if (back) history.back();
      else history.forward();
    },
    true,
  );
  const button = (event: MouseEvent) => {
    if (event.button !== 3 && event.button !== 4) return;
    event.preventDefault();
    if (event.type !== "mouseup") return; // act once, on release
    if (event.button === 3) history.back();
    else history.forward();
  };
  window.addEventListener("mousedown", button, true);
  window.addEventListener("mouseup", button, true);
}
