"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Field } from "@/components/ui";
import { api } from "@/lib/client/api";

interface Chat {
  id: number;
  type: string;
  name: string;
  username: string | null;
}

/** Connect a bot and pick your chat: the chat that sends the code shown here. */
export function TelegramPairing({ onDone, onSkip }: { onDone: () => void; onSkip?: () => void }) {
  const [token, setToken] = useState("");
  const [bot, setBot] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Anyone can message a bot: only the chat that sends this code is offered.
  const [code] = useState(() => {
    const alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
    return Array.from(crypto.getRandomValues(new Uint8Array(6)), (n) => alphabet[n % alphabet.length]).join("");
  });

  useEffect(() => {
    if (!bot) return;
    const timer = setInterval(() => {
      api<{ chats: Chat[] }>("telegram/detect", { query: { code } })
        .then((found) => setChats(found.chats.filter((c) => c.type === "private")))
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [bot, code]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ bot: string }>("secrets/telegram", { method: "PUT", json: { token } });
      setToken("");
      setBot(result.bot);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function choose(chat: Chat) {
    await api("settings", { method: "PATCH", json: { telegram: true, telegram_chat_ids: [chat.id] } });
    onDone();
  }

  return bot ? (
    <div className="space-y-3">
      <Alert tone="good">Connected to {bot}.</Alert>
      <p className="text-sm">
        Now open Telegram and send <strong>{bot}</strong> this code: <strong className="font-mono text-base">{code}</strong>. Your chat shows up here.
      </p>
      {chats.length === 0 ? (
        <p className="text-sm text-muted">Waiting for your message…</p>
      ) : (
        chats.map((chat) => (
          <Button key={chat.id} kind="primary" onClick={() => choose(chat)} className="w-full">
            Use {chat.name}
            {chat.username ? ` (@${chat.username})` : ""}
          </Button>
        ))
      )}
    </div>
  ) : (
    <form onSubmit={save} className="space-y-4" autoComplete="off">
      <Field label="Bot token" type="password" value={token} onChange={setToken} placeholder="123456:ABC…" required />
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex gap-2">
        <Button type="submit" kind="primary" busy={busy} disabled={!token}>
          Connect
        </Button>
        {onSkip ? (
          <Button kind="ghost" onClick={onSkip}>
            Skip for now
          </Button>
        ) : null}
      </div>
    </form>
  );
}

