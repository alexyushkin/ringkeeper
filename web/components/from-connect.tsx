"use client";

import { useState } from "react";

import { Alert, Button, Card, ConfirmButton, Details } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

interface Sent {
  name: string;
  transport: string;
  secrets: number;
  problem: string | null;
  brought_in_as: string | null;
}

interface SentClient {
  name: string;
  exists: boolean;
  projects: string[];
  new_project: string | null;
  connections: Sent[];
}

interface Waiting {
  file: string;
  error?: string;
  sent_at?: string;
  clients?: SentClient[];
  every_client?: Sent[];
}

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/** What Ringkeeper Connect sent with its Send to Ringkeeper button, waiting here: what bringing
 * it in makes, and one click to do it. Nothing shows when nothing was sent. */
export function FromConnect({ onBrought }: { onBrought?: () => void }) {
  const found = useApi<{ waiting: Waiting[] }>(DESKTOP ? "desktop/from-connect" : null);
  const [said, setSaid] = useState<string | null>(null);
  if (!DESKTOP) return null;
  const waiting = found.data?.waiting ?? [];
  if (!waiting.length && !said) return null;
  return (
    <div className="mt-4 space-y-3">
      {said ? <Alert tone="good">{said}</Alert> : null}
      {waiting.map((sent) => (
        <SentCard
          key={sent.file}
          sent={sent}
          onDone={(text) => {
            setSaid(text);
            found.reload();
            onBrought?.();
          }}
        />
      ))}
    </div>
  );
}

function SentCard({ sent, onDone }: { sent: Waiting; onDone: (said: string) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const clients = sent.clients ?? [];
  const every = sent.every_client ?? [];
  const coming = [...clients.flatMap((c) => c.connections), ...every].filter((c) => !c.problem && !c.brought_in_as);
  const left = [...clients.flatMap((c) => c.connections.map((s) => ({ ...s, client: c.name }))), ...every.map((s) => ({ ...s, client: "every client" }))].filter((c) => c.problem);

  async function bringIn() {
    setBusy(true);
    setError(null);
    try {
      const made = await api<{ connections: string[]; clients: string[]; projects: string[]; left: string[] }>("desktop/from-connect/bring-in", { json: { file: sent.file } });
      const parts = [plural(made.connections.length, "connection", "connections")];
      if (made.clients.length) parts.unshift(plural(made.clients.length, "new client", "new clients"));
      const left = made.left.length ? ` ${made.left.length === 1 ? "One" : made.left.length} stayed behind, as the card said: sign in to ${made.left.length === 1 ? "it" : "those"} again from Connectors › Browse connectors.` : "";
      onDone(`Brought in ${parts.join(" and ")} from Ringkeeper Connect.${left}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function throwAway() {
    await api("desktop/from-connect/throw-away", { json: { file: sent.file } });
    onDone("Thrown away. Nothing was brought in.");
  }

  if (sent.error) {
    return (
      <Card className="space-y-2">
        <h2 className="font-semibold">From Ringkeeper Connect</h2>
        <Alert>{sent.error}</Alert>
        <ConfirmButton question="Throw this away? Nothing is brought in." confirm="Throw it away" onConfirm={throwAway}>
          Throw it away
        </ConfirmButton>
      </Card>
    );
  }
  return (
    <Card className="space-y-3 border-accent">
      <h2 className="font-semibold">Ringkeeper Connect sent {plural(clients.length, "client", "clients")}</h2>
      <p className="text-sm text-muted">
        Bringing them in makes each client here (with a project for one that has none), its connections with their keys in Ringkeeper&apos;s vault, and
        the tools for every client as shared tools. A client&apos;s connection starts as Production, so its changes wait for your approval. Connect keeps
        everything too.
      </p>
      <ul className="space-y-1 text-sm">
        {clients.map((c) => (
          <li key={c.name}>
            <span className="font-medium">{c.name}</span>{" "}
            <span className="text-muted">
              · {plural(c.connections.length, "connection", "connections")} · {c.new_project ? `a new project, ${c.new_project}` : c.exists ? `into ${c.projects.join(", ")}` : ""}
            </span>
          </li>
        ))}
        {every.length ? (
          <li>
            <span className="font-medium">For every client</span> <span className="text-muted">· {plural(every.length, "tool", "tools")}</span>
          </li>
        ) : null}
      </ul>
      {left.length ? (
        <Details label={`${left.length} can't come over yet`}>
          <ul className="space-y-1 text-sm">
            {left.map((s) => (
              <li key={`${s.client}:${s.name}`}>
                {s.client}: <span className="font-medium">{s.name}</span> <span className="text-muted">({s.problem})</span>
              </li>
            ))}
          </ul>
        </Details>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!coming.length && !clients.some((c) => !c.exists)} onClick={() => void bringIn()}>
          Bring them in
        </Button>
        <ConfirmButton question="Throw this away? Nothing is brought in; Connect still has everything." confirm="Throw it away" onConfirm={throwAway}>
          Throw it away
        </ConfirmButton>
      </div>
    </Card>
  );
}
