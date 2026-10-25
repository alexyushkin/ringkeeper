"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, Button, Field } from "@/components/ui";
import { ApiError, api, setConfirmHandler } from "@/lib/client/api";

/**
 * Sensitive changes (passkeys, the recovery key, secrets, Telegram chats) need
 * the password again if you signed in more than a few minutes ago. The API
 * says so; this dialog asks, confirms, and the change is retried.
 */
export function ConfirmPassword() {
  const [open, setOpen] = useState(false);
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const settle = useRef<((ok: boolean) => void) | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    setConfirmHandler(
      () =>
        new Promise<boolean>((resolve) => {
          settle.current = resolve;
          setPassword("");
          setError(null);
          setOpen(true);
        }),
    );
    return () => setConfirmHandler(null);
  }, []);

  useEffect(() => {
    if (open) dialog.current?.showModal();
    else dialog.current?.close();
  }, [open]);

  function finish(ok: boolean) {
    setOpen(false);
    setPassword("");
    settle.current?.(ok);
    settle.current = null;
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("auth/confirm", { json: { password } });
      finish(true);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "That password isn't right." : err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <dialog
      ref={dialog}
      onCancel={(event) => {
        event.preventDefault();
        finish(false);
      }}
      className="m-auto w-[min(26rem,calc(100vw-2rem))] rounded-2xl border border-line bg-surface p-5 text-ink backdrop:bg-black/40"
    >
      {/* Only while open: a hidden password field would confuse password managers. */}
      {open ? (
        <form onSubmit={submit} className="space-y-3">
          <h2 className="text-lg font-semibold">Confirm it's you</h2>
          <p className="text-sm text-muted">This change needs your password again.</p>
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="current-password" required />
          {error ? <Alert>{error}</Alert> : null}
          <div className="flex justify-end gap-2">
            <Button kind="ghost" onClick={() => finish(false)}>
              Cancel
            </Button>
            <Button type="submit" kind="primary" busy={busy} disabled={!password}>
              Confirm
            </Button>
          </div>
        </form>
      ) : null}
    </dialog>
  );
}
