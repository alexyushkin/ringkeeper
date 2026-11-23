"use client";

import { useEffect, useState } from "react";

import { Alert, Button, ConfirmButton } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

export interface PlanStatus {
  available: boolean;
  signed_in: boolean;
  email?: string | null;
  noticed?: boolean;
  usage_url?: string;
  notice?: string;
}

export const USAGE_URL = "https://chatgpt.com/settings/usage";

/** "Manage usage": the plan's limits, and the one set for Ringkeeper, are in ChatGPT's settings. */
export function ManageUsage({ url = USAGE_URL }: { url?: string }) {
  return (
    <a className="text-sm font-medium text-accent underline" href={url} target="_blank" rel="noopener noreferrer">
      Manage usage
    </a>
  );
}

/** Your ChatGPT plan, through Sign in with ChatGPT (desktop app only). Signing in opens
 * OpenAI's page in the browser; this checks back until it's done. */
export function ChatGptPlan({ onChanged }: { onChanged?: () => void }) {
  const status = useApi<PlanStatus>("chatgpt");
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!waiting) return;
    if (status.data?.signed_in) {
      setWaiting(false);
      onChanged?.();
      return;
    }
    const started = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - started > 10 * 60 * 1000) setWaiting(false);
      status.reload();
    }, 2000);
    return () => window.clearInterval(timer);
  }, [waiting, status, onChanged]);

  if (!status.data?.available) return null;
  const plan = status.data;

  async function signIn() {
    setError(null);
    try {
      const started = await api<{ url: string }>("chatgpt/signin", { method: "POST" });
      window.open(started.url, "_blank", "noopener");
      setWaiting(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function signOut() {
    setError(null);
    try {
      await api("chatgpt", { method: "DELETE" });
      status.reload();
      onChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function gotIt() {
    await api("chatgpt/noticed", { method: "POST" }).catch(() => undefined);
    status.reload();
  }

  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <p className="text-sm font-medium">Your ChatGPT plan</p>
      {error ? <Alert>{error}</Alert> : null}
      {plan.signed_in ? (
        <>
          {!plan.noticed ? (
            <Alert tone="accent">
              <p className="font-medium">You&apos;re using your ChatGPT plan</p>
              <p>{plan.notice}</p>
              <Button kind="secondary" onClick={gotIt}>
                Got it
              </Button>
            </Alert>
          ) : null}
          <p className="text-sm">
            Signed in{plan.email ? ` as ${plan.email}` : ""}. Choose a ChatGPT model below for sorting requests (it answers in Help too) or for tasks. Tasks on it run on Codex, OpenAI&apos;s agent, with the same connections, approvals and undo.
          </p>
          <div className="flex flex-wrap items-center gap-3">
            <ManageUsage url={plan.usage_url} />
            <ConfirmButton kind="secondary" question="Sign out of ChatGPT? Everything set to a ChatGPT model goes back to its Claude model." confirm="Sign out" onConfirm={signOut}>
              Sign out
            </ConfirmButton>
          </div>
        </>
      ) : (
        <>
          <p className="text-sm text-muted">
            Run tasks, sort requests and answer in Help on your ChatGPT plan instead of Claude. Usage comes out of your plan, and you can set a limit for Ringkeeper in ChatGPT&apos;s settings. Ringkeeper never sees your chats.
          </p>
          <Button kind="primary" busy={waiting} onClick={signIn}>
            Continue with ChatGPT
          </Button>
          {waiting ? <p className="text-xs text-muted">Finish signing in in your browser. This page updates when it&apos;s done.</p> : null}
        </>
      )}
    </div>
  );
}
