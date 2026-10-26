"use client";

import { browserSupportsWebAuthn, startAuthentication } from "@simplewebauthn/browser";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { Alert, Button, Card, Field } from "@/components/ui";
import { ApiError, api } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

/** Only a path on this site: never //evil.example or /\\evil.example. */
function safeNext(value: string): string {
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return "/";
  try {
    const url = new URL(value, window.location.origin);
    return url.origin === window.location.origin ? `${url.pathname}${url.search}${url.hash}` : "/";
  } catch {
    return "/";
  }
}

function LoginForm() {
  const router = useRouter();
  const params = useSearchParams();
  const next = params.get("next") ?? "/";
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [passkeys, setPasskeys] = useState(false);

  useEffect(() => {
    // First run: no owner yet, so go to setup instead.
    api<{ owner: boolean }>("setup/status")
      .then((status) => {
        if (!status.owner) router.replace("/setup");
      })
      .catch(() => undefined);
    setPasskeys(browserSupportsWebAuthn() && window.isSecureContext);
  }, [router]);

  const done = () => router.replace(safeNext(next));

  async function signIn(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("auth/login", { json: { password } });
      done();
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 429
          ? "Too many tries. Wait 15 minutes and try again."
          : err instanceof Error
            ? err.message === "wrong password"
              ? "That password isn't right."
              : err.message
            : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  async function passkey() {
    setBusy(true);
    setError(null);
    try {
      const { challenge_id, options } = await api<{ challenge_id: string; options: Parameters<typeof startAuthentication>[0]["optionsJSON"] }>(
        "auth/passkeys/login/options",
        { method: "POST" },
      );
      const credential = await startAuthentication({ optionsJSON: options });
      await api("auth/passkeys/login", { json: { challenge_id, credential } });
      done();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-4">
      <div className="mb-6 flex items-center gap-2 text-2xl font-semibold">
        <span aria-hidden className="inline-block size-6 rounded-full border-4 border-accent" />
        Ringkeeper
      </div>
      <Card>
        <form onSubmit={signIn} className="space-y-4">
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" required />
          {error ? <Alert>{error}</Alert> : null}
          <Button type="submit" kind="primary" busy={busy} className="w-full">
            Sign in
          </Button>
          {passkeys ? (
            <Button onClick={passkey} disabled={busy} className="w-full">
              Sign in with a passkey
            </Button>
          ) : null}
        </form>
      </Card>
    </main>
  );
}

/** The desktop app has no sign-in: the window is the key. It lands here only
 * when its key no longer fits (Ringkeeper was restarted without the app). */
function Reopen() {
  return (
    <main className="mx-auto max-w-sm px-4 py-16">
      <Card className="space-y-3">
        <h1 className="text-lg font-semibold">Open Ringkeeper again</h1>
        <p className="text-sm">This window lost its connection to Ringkeeper. Close it, then open Ringkeeper from the tray icon or the Start menu.</p>
      </Card>
    </main>
  );
}

export default function LoginPage() {
  if (DESKTOP) return <Reopen />;
  return (
    <Suspense>
      <LoginForm />
    </Suspense>
  );
}
