"use client";

import Link from "@/components/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Composer } from "@/components/composer";
import { BulkBar, ListTools, SelectBox, useSelection } from "@/components/tidy";
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
  archived: boolean;
}

const SOURCE: Record<string, string> = { web: "app", api: "API", cli: "terminal", telegram: "Telegram" };

export default function Home() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [archived, setArchived] = useState(false);
  const threads = useApi<Thread[]>("threads", { q: query.trim() || undefined, archived: archived || undefined });
  const pick = useSelection();
  useLive((event) => {
    if (["message", "message_routed", "task", "approval"].includes(event.type)) threads.reload();
  });

  return (
    <>
      <PageHeader title="What do you need?" subtitle="Say it in your own words. Ringkeeper sends it to the right client project." />
      <Composer onSent={(thread) => router.push(threadHref(thread))} />
      <h2 className="mt-8 mb-3 text-sm font-semibold text-muted">{archived ? "Archived conversations" : "Recent conversations"}</h2>
      <ListTools query={query} onQuery={setQuery} archived={archived} onArchived={(on) => { setArchived(on); pick.clear(); }} what="conversations" />
      <BulkBar kind="thread" ids={[...pick.selected]} archivedView={archived} onDone={threads.reload} onClear={pick.clear} />
      {threads.error ? <Alert>{threads.error}</Alert> : null}
      {threads.loading && !threads.data ? <Loading /> : null}
      {threads.data?.length === 0 ? <Empty>{query || archived ? "Nothing matches." : "Nothing yet. Your requests and their results show up here."}</Empty> : null}
      <ul className="space-y-2">
        {threads.data?.map((thread) => {
          const running = (thread.tasks.running ?? 0) + (thread.tasks.queued ?? 0);
          const done = thread.tasks.succeeded ?? 0;
          const failed = (thread.tasks.failed ?? 0) + (thread.tasks.timed_out ?? 0);
          return (
            <li key={thread.id} className="flex items-start gap-3">
              <span className="pt-5">
                <SelectBox checked={pick.has(thread.id)} onChange={() => pick.toggle(thread.id)} label={`Select ${thread.title}`} />
              </span>
              <Link href={threadHref(thread.id)} className="block min-w-0 flex-1 rounded-2xl border border-line bg-surface p-4 hover:border-accent">
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
                  {thread.archived ? <Badge>archived</Badge> : null}
                </div>
              </Link>
            </li>
          );
        })}
      </ul>
    </>
  );
}
