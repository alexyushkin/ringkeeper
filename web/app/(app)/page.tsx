"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

import { Composer } from "@/components/composer";
import { Alert, Badge, Empty, Loading, PageHeader, timeAgo } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";
import { threadHref } from "@/lib/client/routes";

interface Thread {
  id: string;
  title: string;
  source: string;
  updated_at: string;
  last_message: string | null;
  tasks: Record<string, number>;
  pending_approvals: number;
}

const SOURCE: Record<string, string> = { web: "app", api: "API", cli: "terminal", telegram: "Telegram" };

export default function Home() {
  const router = useRouter();
  const threads = useApi<Thread[]>("threads");
  useLive((event) => {
    if (["message", "message_routed", "task", "approval"].includes(event.type)) threads.reload();
  });

  return (
    <>
      <PageHeader title="What do you need?" subtitle="Say it in your own words. Ringkeeper sends it to the right client project." />
      <Composer onSent={(thread) => router.push(threadHref(thread))} />
      <h2 className="mt-8 mb-3 text-sm font-semibold text-muted">Recent conversations</h2>
      {threads.error ? <Alert>{threads.error}</Alert> : null}
      {threads.loading && !threads.data ? <Loading /> : null}
      {threads.data?.length === 0 ? <Empty>Nothing yet. Your requests and their results show up here.</Empty> : null}
      <ul className="space-y-2">
        {threads.data?.map((thread) => {
          const running = (thread.tasks.running ?? 0) + (thread.tasks.queued ?? 0);
          const done = thread.tasks.succeeded ?? 0;
          const failed = (thread.tasks.failed ?? 0) + (thread.tasks.timed_out ?? 0);
          return (
            <li key={thread.id}>
              <Link href={threadHref(thread.id)} className="block rounded-2xl border border-line bg-surface p-4 hover:border-accent">
                <div className="flex items-center gap-2">
                  <span className="truncate font-medium">{thread.title}</span>
                  <span className="ml-auto shrink-0 text-xs text-muted">{timeAgo(thread.updated_at)}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {thread.pending_approvals ? <Badge tone="warn">{thread.pending_approvals} waiting for you</Badge> : null}
                  {running ? <Badge tone="accent">{running} working</Badge> : null}
                  {done ? <Badge tone="good">{done} done</Badge> : null}
                  {failed ? <Badge tone="bad">{failed} failed</Badge> : null}
                  <Badge>from {SOURCE[thread.source] ?? thread.source}</Badge>
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}
