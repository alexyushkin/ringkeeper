"use client";

import { useEffect, useState } from "react";

import Link from "@/components/link";
import { Alert, Button } from "@/components/ui";

/**
 * A sign-in through your own developer app (Meta, Microsoft, Google, HubSpot...): which
 * app it uses, the errors the vendor's page shows most and what fixes each, and a wait
 * that ends after 2 minutes instead of saying "finish signing in" for good.
 */

export const SIGNIN_WAIT_MS = 2 * 60_000;

export interface Trouble {
  shown: string;
  fix: string;
}

export interface AppShown {
  name: string;
  client_id: string;
  page?: string; // its dashboard at the vendor
}

/** "Signs in through <app> (app ID ...)" with its dashboard, and "<vendor> shows an error?". */
export function ThroughApp({ vendor, app, troubles, open = false }: { vendor: string; app?: AppShown; troubles: Trouble[]; open?: boolean }) {
  // Folded until asked, and opened by itself when the wait runs out (`open`).
  const [shown, setShown] = useState(open);
  useEffect(() => {
    if (open) setShown(true);
  }, [open]);
  return (
    <div className="space-y-1 text-sm">
      {app ? (
        <p className="text-muted">
          Signs in through your app <b className="text-ink">{app.name}</b> (app ID <code className="select-all break-all">{app.client_id}</code>)
          {app.page ? (
            <>
              {" "}·{" "}
              <a href={app.page} target="_blank" rel="noreferrer" className="text-accent">
                Open its dashboard
              </a>
            </>
          ) : null}
          {" "}·{" "}
          <Link href="/settings#developer-apps" className="text-accent">
            Change it in Settings › Developer apps
          </Link>
        </p>
      ) : null}
      {troubles.length ? (
        <div>
          <button type="button" onClick={() => setShown(!shown)} className="text-sm text-accent" aria-expanded={shown}>
            {vendor} shows an error?
          </button>
          {shown ? (
            <ul className="mt-2 space-y-1.5">
              {troubles.map((t) => (
                <li key={t.shown}>
                  <b>&quot;{t.shown}&quot;</b>: {t.fix}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** Waiting on the vendor's page: true once SIGNIN_WAIT_MS has passed since `since`. */
export function useTimedOut(since: number | null): boolean {
  const [out, setOut] = useState(false);
  useEffect(() => {
    setOut(false);
    if (since === null) return;
    const timer = setTimeout(() => setOut(true), Math.max(0, since + SIGNIN_WAIT_MS - Date.now()));
    return () => clearTimeout(timer);
  }, [since]);
  return out;
}

/** While waiting: "Finish signing in"; after 2 minutes: it stopped waiting, and Try again. */
export function SignInWaiting({ vendor, timedOut, onTryAgain, busy }: { vendor: string; timedOut: boolean; onTryAgain: () => void; busy?: boolean }) {
  if (!timedOut) return <Alert tone="good">Finish signing in on {vendor}&apos;s page that opened in your browser. This updates when it&apos;s done.</Alert>;
  return (
    <div className="space-y-2">
      <Alert tone="warn">The sign-in didn&apos;t finish in 2 minutes, so this stopped waiting. If {vendor}&apos;s page showed an error, the fixes are below. Then try again.</Alert>
      <Button kind="primary" busy={busy} onClick={onTryAgain}>
        Try again
      </Button>
    </div>
  );
}
