"use client";

import { HelpExtras, type HelpAnswer } from "@/components/help-answer";
import Link from "@/components/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { Composer } from "@/components/composer";
import { Changes } from "@/components/changes";
import { type Decision } from "@/components/decision-card";
import { Markdown, plainText } from "@/components/markdown";
import { AddConnection, type Missing } from "@/components/missing-connection";
import { TidyButtons } from "@/components/tidy";
import { TaskCardView, type TaskCard } from "@/components/task-card";
import { RememberThis, SavedToMemory } from "@/components/memory";
import { TaskFiles } from "@/components/task-files";
import { Waiting } from "@/components/waiting";
import { CancelButton } from "@/components/scheduled";
import { Alert, Badge, Button, Card, Details, Loading, clock, timeAgo } from "@/components/ui";
import { RESYNC, api, useApi, useLive } from "@/lib/client/api";
import { threadHref } from "@/lib/client/routes";
import { projectName } from "@/lib/client/scope";
import { routingLine } from "@/lib/routing-wait";
import { throttle } from "@/lib/throttle";

interface Message {
  id: string;
  role: string;
  author: string;
  source: string;
  input: string;
  body: string;
  routing: { output?: { tasks?: { project: string; kind: string; confidence: number; outcome: string }[] }; help?: HelpAnswer; missing?: Missing[]; ran?: boolean; retry?: string; claimed_at?: string; retry_after?: string; waiting?: string } | null;
  route_error: string | null;
  run_at?: string | null;
  canceled_at?: string | null;
  created_at: string;
}

interface Thread {
  id: string;
  title: string;
  messages: Message[];
  archived?: boolean;
}

/** "Routing…" at once; after 10 seconds with no reply, "No reply after 10 seconds." and Retry,
 * which fetches the conversation again and wakes the router. Never silence. */
function RoutingLine({ id, sentAt, onRetry }: { id: string; sentAt: string; onRetry: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const line = routingLine(sentAt, now);
  useEffect(() => {
    if (line.changesIn === null) return;
    const timer = setTimeout(() => setNow(Date.now()), line.changesIn);
    return () => clearTimeout(timer);
  }, [line.changesIn]);
  async function retry() {
    setBusy(true);
    try {
      await api(`messages/${id}/nudge`, { method: "POST" });
    } catch {
      // The conversation is fetched again either way.
    } finally {
      onRetry();
      setBusy(false);
    }
  }
  return (
    <p data-routing className="mt-1 ml-8 flex flex-wrap items-center gap-2 text-xs text-muted">
      {line.text}
      {line.retry ? (
        <Button busy={busy} onClick={() => void retry()}>
          Retry
        </Button>
      ) : null}
    </p>
  );
}

/** A request still waiting for its reply (not one sent for later, or canceled, or failed). */
function awaitingReply(message: Message): boolean {
  if (message.role !== "user" || message.canceled_at || message.route_error) return false;
  if (message.run_at && new Date(message.run_at).getTime() > Date.now()) return false;
  return !message.routing || Boolean(message.routing.claimed_at);
}

const POLL_MS = 5_000;

export function ThreadView({ id }: { id: string }) {
  const router = useRouter();
  const thread = useApi<Thread>(`threads/${id}`);
  const tasks = useApi<TaskCard[]>(`threads/${id}/tasks`);
  const pending = useApi<Decision[]>("decisions", { status: "pending", thread: id, limit: 500 });
  const reloadAll = () => {
    thread.reload();
    tasks.reload();
    pending.reload();
  };
  // A task's steps go up with each tool call: its transcript lines reload the cards a little.
  const recount = useMemo(() => throttle(tasks.reload, 1_500), [tasks.reload]);
  useLive((event) => {
    if (event.type === RESYNC) return reloadAll(); // events may have been missed
    if (event.type === "session_event" && (tasks.data ?? []).some((t) => t.id === event.task_id)) return recount();
    if (event.thread_id !== id) return;
    if (event.type === "message" || event.type === "message_routed") thread.reload();
    if (event.type === "task" || event.type === "message_routed") tasks.reload();
    if (event.type === "approval" || event.type === "message_routed") pending.reload();
  });
  // While a reply or a task is on its way, fetch it every few seconds too: what it shows
  // never depends on one live event arriving.
  const inFlight =
    (thread.data?.messages ?? []).some(awaitingReply) || (tasks.data ?? []).some((t) => ["queued", "running"].includes(t.status));
  useEffect(() => {
    if (!inFlight) return;
    const timer = setInterval(() => {
      thread.reload();
      tasks.reload();
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [inFlight, thread.reload, tasks.reload]);
  // This conversation's questions, and the holds of its tasks.
  const mine = new Set((tasks.data ?? []).map((t) => t.id));
  const waiting = pending.data?.filter((d) => d.thread_id === id || (d.task_id !== null && mine.has(d.task_id))) ?? [];

  if (thread.error) return <Alert>{thread.error}</Alert>;
  if (!thread.data) return <Loading />;

  return (
    <>
      <Link href="/" className="text-sm text-accent">
        ‹ All conversations
      </Link>
      <div className="mt-2 mb-4 flex flex-wrap items-center gap-2">
        <h1 className="text-xl font-semibold">{thread.data.title}</h1>
        {thread.data.archived ? <Badge>archived</Badge> : null}
        <span className="ml-auto" />
        <TidyButtons kind="thread" id={id} archived={Boolean(thread.data.archived)} onArchived={thread.reload} onDeleted={() => router.push("/")} />
      </div>

      <ol className="space-y-3">
        {thread.data.messages.map((message) => (
          <li key={message.id}>
            {message.role === "user" ? (
              <div className="ml-8 rounded-2xl bg-accent-soft p-3">
                <p className="whitespace-pre-wrap">{message.body}</p>
                <p className="mt-1 text-xs text-muted">
                  {message.input === "voice" ? "Spoken · " : ""}
                  {timeAgo(message.created_at)}
                </p>
              </div>
            ) : (
              <Card>
                <p className="text-xs font-medium text-muted">Ringkeeper</p>
                <Markdown text={message.body} className="mt-1 text-sm" />
              {message.role === "router" && !message.routing?.missing?.length && !message.routing?.ran ? <RememberThis text={message.body} projects={[...new Set((tasks.data ?? []).map((t) => t.project))]} /> : null}
              {message.routing?.help ? <HelpExtras help={message.routing.help} /> : null}
              {message.routing?.retry ? <TryAgain id={message.routing.retry} onDone={thread.reload} /> : null}
              {(message.routing?.missing ?? []).map((missing) => (
                <AddConnection key={`${missing.project}:${missing.need}`} missing={missing} back={threadHref(id)} run={missing.message ?? ""} onRan={() => { thread.reload(); tasks.reload(); }} />
              ))}
              </Card>
            )}
            {message.role === "user" && message.routing?.output?.tasks?.length ? (
              <div className="mt-2 ml-8">
                <Details label="How it was split">
                  <ul className="space-y-1 text-sm">
                    {message.routing.output.tasks.map((task, index) => (
                      <li key={index}>
                        <Badge>{projectName(task.project)}</Badge> {plainText(task.outcome)}{" "}
                        <span className="text-xs text-muted">
                          ({task.kind}, {Math.round(task.confidence * 100)}% sure)
                        </span>
                      </li>
                    ))}
                  </ul>
                </Details>
              </div>
            ) : null}
            {message.role === "user" && message.canceled_at ? (
              <p className="mt-1 ml-8 text-xs text-muted">Not run.</p>
            ) : message.role === "user" && message.routing?.retry_after && !message.route_error ? (
              <p className="mt-1 ml-8 text-xs text-muted">Waiting for Claude: it tries again {clock(message.routing.retry_after)}.</p>
            ) : message.role === "user" && message.run_at && !message.routing && !message.route_error && new Date(message.run_at).getTime() > Date.now() ? (
              <p data-later className="mt-1 ml-8 flex flex-wrap items-center gap-2 text-xs text-muted">
                Sent for later: it runs {clock(message.run_at)}.
                <CancelButton id={message.id} onDone={thread.reload} />
              </p>
            ) : message.role === "user" && (!message.routing || message.routing.claimed_at) && !message.route_error ? (
              <RoutingLine id={message.id} sentAt={message.created_at} onRetry={reloadAll} />
            ) : null}
          </li>
        ))}
      </ol>


      {tasks.data?.length ? (
        <>
          <h2 className="mt-6 mb-2 text-sm font-semibold text-muted">Work</h2>
          <div className="space-y-2">
            {tasks.data.map((task) => (
              <div key={task.id}>
                <TaskCardView task={task} />
                {task.files && !["queued", "running"].includes(task.status) ? (
                  <div className="px-4">
                    <TaskFiles id={task.id} compact />
                  </div>
                ) : null}
                {["queued", "running"].includes(task.status) ? null : (
                  <div className="space-y-1 px-4 pt-1">
                    <SavedToMemory task={task.id} />
                    {task.summary ? <RememberThis text={task.summary} projects={[task.project]} task={task.id} /> : null}
                  </div>
                )}
              </div>
            ))}
          </div>
        </>
      ) : null}

      {/* Under the latest result, where the reader is. */}
      <Waiting decisions={waiting} onDone={pending.reload} />

      <Changes thread={id} />

      <div className="mt-6">
        <Composer threadId={id} onSent={reloadAll} />
      </div>
    </>
  );
}

/** Routing failed for something other than Claude's side: send the same request again. */
function TryAgain({ id, onDone }: { id: string; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function retry() {
    setBusy(true);
    setError(null);
    try {
      await api(`messages/${id}/retry`, { method: "POST" });
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="mt-2 space-y-2">
      <Button busy={busy} onClick={() => void retry()}>
        Try again
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
