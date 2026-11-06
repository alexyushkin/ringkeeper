"use client";

import { HelpExtras, type HelpAnswer } from "@/components/help-answer";
import Link from "@/components/link";
import { useRouter } from "next/navigation";

import { Composer } from "@/components/composer";
import { Changes } from "@/components/changes";
import { DecisionCard, type Decision } from "@/components/decision-card";
import { Markdown, plainText } from "@/components/markdown";
import { TidyButtons } from "@/components/tidy";
import { TaskCardView, type TaskCard } from "@/components/task-card";
import { Alert, Badge, Card, Details, Loading, timeAgo } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";
import { projectName } from "@/lib/client/scope";

interface Message {
  id: string;
  role: string;
  author: string;
  source: string;
  input: string;
  body: string;
  routing: { output?: { tasks?: { project: string; kind: string; confidence: number; outcome: string }[] }; help?: HelpAnswer } | null;
  route_error: string | null;
  created_at: string;
}

interface Thread {
  id: string;
  title: string;
  messages: Message[];
  archived?: boolean;
}

export function ThreadView({ id }: { id: string }) {
  const router = useRouter();
  const thread = useApi<Thread>(`threads/${id}`);
  const tasks = useApi<TaskCard[]>(`threads/${id}/tasks`);
  const pending = useApi<Decision[]>("decisions", { status: "pending", thread: id, limit: 500 });
  useLive((event) => {
    if (event.thread_id !== id) return;
    if (event.type === "message" || event.type === "message_routed") thread.reload();
    if (event.type === "task" || event.type === "message_routed") tasks.reload();
    if (event.type === "approval" || event.type === "message_routed") pending.reload();
  });
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
              {message.routing?.help ? <HelpExtras help={message.routing.help} /> : null}
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
            {message.role === "user" && !message.routing && !message.route_error ? (
              <p className="mt-1 ml-8 text-xs text-muted">Finding the right project…</p>
            ) : null}
          </li>
        ))}
      </ol>

      {waiting.length ? (
        <div className="mt-4 space-y-3">
          {waiting.map((d) => (
            <DecisionCard key={d.id} decision={d} onDone={pending.reload} />
          ))}
        </div>
      ) : null}

      {tasks.data?.length ? (
        <>
          <h2 className="mt-6 mb-2 text-sm font-semibold text-muted">Work</h2>
          <div className="space-y-2">
            {tasks.data.map((task) => (
              <TaskCardView key={task.id} task={task} />
            ))}
          </div>
        </>
      ) : null}

      <Changes thread={id} />

      <div className="mt-6">
        <Composer threadId={id} onSent={() => thread.reload()} />
      </div>
    </>
  );
}
