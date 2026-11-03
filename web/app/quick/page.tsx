"use client";

import { useEffect, useState } from "react";

import { Composer } from "@/components/composer";
import { ScopeSwitcher } from "@/components/scope-switcher";
import { Button } from "@/components/ui";
import { api } from "@/lib/client/api";
import { threadHref } from "@/lib/client/routes";
import { ScopeProvider, useScope } from "@/lib/client/scope";
import { closeQuickBox } from "@/lib/client/shell";

const TOAST_SECONDS = 8;

/** The hotkey's small window: say what you need, send, and it closes. It stays a moment
 * as its own toast, with a link to the conversation. */
export default function QuickPage() {
  return (
    <ScopeProvider>
      <Quick />
    </ScopeProvider>
  );
}

function Quick() {
  const scope = useScope();
  const [sent, setSent] = useState<string | null>(null);
  // Chosen in the switcher: named in the request, so it's a firm choice like any @.
  const named = scope.project || scope.client;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") void closeQuickBox();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!sent) return;
    const timer = setTimeout(() => void closeQuickBox(), TOAST_SECONDS * 1000);
    return () => clearTimeout(timer);
  }, [sent]);

  if (sent) {
    return (
      <main className="grid min-h-dvh place-items-center bg-surface p-6 text-center">
        <div className="space-y-3">
          <p className="font-medium">Sent to Ringkeeper.</p>
          <Button
            kind="primary"
            onClick={() => void api("desktop/open", { json: { path: threadHref(sent) } }).finally(() => void closeQuickBox())}
          >
            Open the conversation
          </Button>
        </div>
      </main>
    );
  }
  return (
    <main className="min-h-dvh bg-surface">
      <ScopeSwitcher />
      <div className="p-3">
        <Composer autoFocus prefix={named ? `@${named} ` : ""} onSent={setSent} />
      </div>
    </main>
  );
}
