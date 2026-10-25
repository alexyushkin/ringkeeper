"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, Badge, Button, Card, Code, Details, Loading, StatusBadge, TextArea, money, timeAgo } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { threadHref } from "@/lib/client/routes";

interface Session {
  id: string;
  ordinal: number;
  role: string;
  model: string | null;
  status: string;
  turns: number | null;
  cost_usd: number | null;
  prompt: string;
  checks: { passed?: boolean; results?: { command: string; passed: boolean; exit_code: number; output: string }[] } | null;
  result: string | null;
  error: string | null;
  created_at: string;
}

interface Task {
  id: string;
  project: string;
  status: string;
  kind: string | null;
  outcome: string | null;
  summary: string | null;
  error: string | null;
  prompt: string;
  thread_id: string | null;
  branch: string | null;
  changed_files: string[];
  diff: string | null;
  turns: number;
  cost_usd: number | null;
  created_at: string;
  finished_at: string | null;
  session_list: Session[];
}

interface Event {
  id: number;
  session: number;
  role: string;
  type: string;
  at: string;
  payload: Record<string, unknown>;
}

type Block = { _kind?: string; text?: string; name?: string; input?: unknown; content?: unknown; is_error?: boolean };

const ACTIVE = new Set(["queued", "running"]);
const RESUMABLE = new Set(["succeeded", "failed", "stopped", "timed_out", "budget_exceeded", "needs_input"]);

function blocks(payload: Record<string, unknown>): Block[] {
  const message = payload.message as { content?: unknown } | undefined;
  return Array.isArray(message?.content) ? (message.content as Block[]) : [];
}

function asText(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map((v) => (typeof v === "object" && v && "text" in v ? String((v as { text: unknown }).text) : JSON.stringify(v))).join("\n");
  return JSON.stringify(value, null, 2);
}

/** One transcript line: what the assistant said, which tools it used, what came back. */
function EventView({ event }: { event: Event }) {
  const p = event.payload;
  switch (event.type) {
    case "sdk.assistant":
      return (
        <>
          {blocks(p).map((block, index) =>
            block._kind === "TextBlock" && block.text?.trim() ? (
              <p key={index} className="text-sm whitespace-pre-wrap">
                {block.text}
              </p>
            ) : block._kind === "ToolUseBlock" ? (
              <div key={index} className="rounded-xl bg-sunken p-2 text-sm">
                <span className="font-medium">Used {block.name}</span>
                <Details label="Show arguments">
                  <Code>{asText(block.input)}</Code>
                </Details>
              </div>
            ) : null,
          )}
        </>
      );
    case "sdk.user":
      return (
        <>
          {blocks(p)
            .filter((b) => b._kind === "ToolResultBlock")
            .map((block, index) => (
              <div key={index} className={`rounded-xl p-2 text-sm ${block.is_error ? "bg-bad-soft" : "bg-sunken"}`}>
                <span className="text-muted">{block.is_error ? "Tool error" : "Result"}</span>
                <Details label="Show result">
                  <Code>{asText(block.content)}</Code>
                </Details>
              </div>
            ))}
        </>
      );
    case "ringkeeper.checks": {
      const results = (p.results as { command: string; passed: boolean; output: string }[] | undefined) ?? [];
      return (
        <div className="space-y-1">
          {results.map((r, i) => (
            <div key={i} className="text-sm">
              <Badge tone={r.passed ? "good" : "bad"}>{r.passed ? "Check passed" : "Check failed"}</Badge> <code className="text-xs">{r.command}</code>
              {!r.passed ? <Code>{r.output}</Code> : null}
            </div>
          ))}
        </div>
      );
    }
    case "ringkeeper.ask_user":
      return <Alert tone="accent">Asked you: {String(p.question ?? "")}</Alert>;
    case "ringkeeper.workspace": {
      const files = (p.changed_files as string[] | undefined) ?? [];
      return <p className="text-xs text-muted">{files.length ? `Changed ${files.length} file${files.length > 1 ? "s" : ""}${p.branch ? ` on ${String(p.branch)}` : ""}` : "No files changed"}</p>;
    }
    case "ringkeeper.error":
      return <Alert>{String(p.message ?? "")}</Alert>;
    case "sdk.result": {
      const message = (p.message as { subtype?: string; num_turns?: number } | undefined) ?? {};
      return <p className="text-xs text-muted">Session ended: {message.subtype} after {message.num_turns} steps</p>;
    }
    default:
      return null;
  }
}

export function TaskView({ id }: { id: string }) {
  const task = useApi<Task>(`tasks/${id}`);
  const [events, setEvents] = useState<Event[]>([]);
  const [retention, setRetention] = useState(90);
  const last = useRef(0);
  const [tab, setTab] = useState<"transcript" | "changes">("transcript");
  const [resume, setResume] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const inFlight = useRef(false);
  const again = useRef(false);
  // New lines arrive in bursts (one live event each): fetch one page at a time,
  // and once more afterwards if something came in meanwhile.
  const more = useCallback(async () => {
    if (inFlight.current) {
      again.current = true;
      return;
    }
    inFlight.current = true;
    try {
      do {
        again.current = false;
        const page = await api<{ events: Event[]; retention_days: number }>(`tasks/${id}/events`, { query: { after: last.current, limit: 500 } });
        setRetention(page.retention_days);
        if (page.events.length) {
          last.current = page.events[page.events.length - 1]!.id;
          setEvents((all) => [...all, ...page.events]);
        }
        if (page.events.length === 500) again.current = true;
      } while (again.current);
    } finally {
      inFlight.current = false;
    }
  }, [id]);

  useEffect(() => {
    void more();
  }, [more]);
  useLive((event) => {
    if (event.task_id !== id) return;
    if (event.type === "session_event") void more();
    if (event.type === "task") task.reload();
  });

  async function act(path: string, body?: unknown) {
    setBusy(true);
    setError(null);
    try {
      await api(`tasks/${id}/${path}`, { json: body ?? {} });
      setResume("");
      task.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (task.error) return <Alert>{task.error}</Alert>;
  if (!task.data) return <Loading />;
  const t = task.data;

  return (
    <>
      <Link href={t.thread_id ? threadHref(t.thread_id) : "/tasks"} className="text-sm text-accent">
        ‹ Back
      </Link>
      <Card className="mt-2 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-lg font-semibold">{t.project}</h1>
          <StatusBadge status={t.status} />
          {t.kind ? <Badge>{t.kind}</Badge> : null}
        </div>
        <p className="text-sm">{t.summary ?? t.outcome ?? t.prompt}</p>
        {t.error && t.status !== "succeeded" ? <Alert>{t.error}</Alert> : null}
        <dl className="grid grid-cols-2 gap-2 text-sm sm:grid-cols-4">
          <div>
            <dt className="text-xs text-muted">Steps</dt>
            <dd>{t.turns}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Cost (estimate)</dt>
            <dd>{money(t.cost_usd)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Started</dt>
            <dd>{timeAgo(t.created_at)}</dd>
          </div>
          <div>
            <dt className="text-xs text-muted">Branch</dt>
            <dd className="truncate">{t.branch ?? "–"}</dd>
          </div>
        </dl>
        <Details>
          <ul className="space-y-1 text-xs">
            {t.session_list.map((s) => (
              <li key={s.id}>
                #{s.ordinal} {s.role} · {s.model ?? "default model"} · {s.status} · {s.turns ?? 0} steps · {money(s.cost_usd)}
              </li>
            ))}
          </ul>
        </Details>
        <div className="flex flex-wrap gap-2">
          {ACTIVE.has(t.status) || t.status === "waiting" ? (
            <Button kind="danger" busy={busy} onClick={() => act("stop")}>
              Stop
            </Button>
          ) : null}
        </div>
        {RESUMABLE.has(t.status) ? (
          <div className="space-y-2">
            <TextArea label="Continue this task" value={resume} onChange={setResume} rows={2} hint="Tell it what to do next; it picks up where it left off." />
            <Button busy={busy} disabled={!resume.trim()} onClick={() => act("resume", { prompt: resume })}>
              Resume
            </Button>
          </div>
        ) : null}
        {error ? <Alert>{error}</Alert> : null}
      </Card>

      <div className="mt-4 flex gap-2" role="tablist">
        <Button kind={tab === "transcript" ? "primary" : "secondary"} onClick={() => setTab("transcript")}>
          Transcript
        </Button>
        <Button kind={tab === "changes" ? "primary" : "secondary"} onClick={() => setTab("changes")}>
          Changes ({t.changed_files.length})
        </Button>
      </div>

      {tab === "transcript" ? (
        <ol className="mt-3 space-y-3">
          {events.length === 0 ? <p className="text-sm text-muted">No transcript yet. Transcripts are kept {retention} days.</p> : null}
          {events.map((event) => (
            <li key={event.id} className={event.role === "architect" ? "border-l-2 border-accent pl-3" : ""}>
              <EventView event={event} />
            </li>
          ))}
        </ol>
      ) : (
        <div className="mt-3 space-y-3">
          <ul className="text-sm">
            {t.changed_files.map((file) => (
              <li key={file}>
                <code>{file}</code>
              </li>
            ))}
          </ul>
          {t.diff ? <Code>{t.diff}</Code> : <p className="text-sm text-muted">No diff recorded.</p>}
        </div>
      )}
    </>
  );
}
