"use client";

import Link from "next/link";

import { Composer } from "@/components/composer";
import { TaskCardView, type TaskCard } from "@/components/task-card";
import { Alert, Badge, Card, Details, Loading, timeAgo } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";

interface Message {
  id: string;
  role: string;
  author: string;
  source: string;
  input: string;
  body: string;
  routing: { output?: { tasks?: { project: string; kind: string; confidence: number; outcome: string }[] } } | null;
  route_error: string | null;
  created_at: string;
}

interface Thread {
  id: string;
  title: string;
  messages: Message[];
}

interface Decision {
  id: string;
  kind: string;
  summary: string;
  thread_id: string | null;
}

export function ThreadView({ id }: { id: string }) {
  const thread = useApi<Thread>(`threads/${id}`);
  const tasks = useApi<TaskCard[]>(`threads/${id}/tasks`);
  const pending = useApi<Decision[]>("decisions", { status: "pending", limit: 200 });
  useLive((event) => {
    if (event.thread_id !== id) return;
    if (event.type === "message" || event.type === "message_routed") thread.reload();
    if (event.type === "task" || event.type === "message_routed") tasks.reload();
    if (event.type === "approval") pending.reload();
  });
  const waiting = pending.data?.filter((d) => d.thread_id === id) ?? [];

  if (thread.error) return <Alert>{thread.error}</Alert>;
  if (!thread.data) return <Loading />;

  return (
    <>
      <Link href="/" className="text-sm text-accent">
        ‹ All conversations
      </Link>
      <h1 className="mt-2 mb-4 text-xl font-semibold">{thread.data.title}</h1>

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
                <p className="mt-1 text-sm whitespace-pre-wrap">{message.body}</p>
              </Card>
            )}
            {message.role === "user" && message.routing?.output?.tasks?.length ? (
              <div className="mt-2 ml-8">
                <Details label="How it was split">
                  <ul className="space-y-1 text-sm">
                    {message.routing.output.tasks.map((task, index) => (
                      <li key={index}>
                        <Badge>{task.project}</Badge> {task.outcome}{" "}
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
        <Card className="mt-4 border-warn">
          <p className="text-sm font-medium">Waiting for you</p>
          <ul className="mt-2 space-y-1 text-sm">
            {waiting.map((d) => (
              <li key={d.id}>
                <Link href={`/approvals#${d.id}`} className="text-accent">
                  {d.summary} ›
                </Link>
              </li>
            ))}
          </ul>
        </Card>
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

      <div className="mt-6">
        <Composer threadId={id} onSent={() => thread.reload()} />
      </div>
    </>
  );
}
