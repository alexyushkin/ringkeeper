"use client";

import Link from "@/components/link";

import { timeAgo } from "@/components/ui";
import { useApi, useLive } from "@/lib/client/api";
import { money } from "@/components/client-month";
import { clientHref, routineHref } from "@/lib/client/routes";

interface Watching {
  watching: number;
  last: { routine: string; routine_id: string; what: string; at: string } | null;
  cant_look: { routine: string; routine_id: string; error: string }[];
  capped?: { client: string; name: string; used: number; cap: number; routines: number }[];
}

/** Home's Automation watch line: n8n runs, Vercel deploys, GitHub checks and issues, and
 * websites that routines watch; the last problem found; any watch that can't look. */
export function WatchLine({ client, project }: { client?: string; project?: string }) {
  const data = useApi<Watching>("watching", { client, project });
  useLive((event) => {
    if (event.type === "task") data.reload(); // a watch's run is a task
  });
  const seen = data.data;
  const capped = seen?.capped ?? [];
  if (!seen?.watching && !capped.length) return null;
  return (
    <>
      {capped.map((c) => (
        <p key={c.client} className="mt-4 rounded-2xl border border-line bg-surface px-4 py-3 text-sm">
          <span className="font-medium">{c.name}&apos;s routines are waiting.</span> {c.name} is over its monthly cap ({money(c.used)} of {money(c.cap)}), so{" "}
          {c.routines === 1 ? "its routine skips its runs" : `its ${c.routines} routines skip their runs`} until you{" "}
          <Link href={`${clientHref(c.client)}#settings`} className="text-accent underline">
            raise the cap
          </Link>{" "}
          or the month ends.
        </p>
      ))}
      {seen?.watching ? <Watched seen={seen} /> : null}
    </>
  );
}

function Watched({ seen }: { seen: Watching }) {
  const [first] = seen.cant_look;
  return (
    <p className="mt-4 rounded-2xl border border-line bg-surface px-4 py-3 text-sm">
      <span className="font-medium">Automation watch:</span> {seen.watching === 1 ? "1 routine is watching" : `${seen.watching} routines are watching`}.{" "}
      {seen.last ? (
        <>
          Last problem {timeAgo(seen.last.at)}:{" "}
          <Link href={routineHref(seen.last.routine_id)} className="text-accent underline">
            {seen.last.what}
          </Link>
        </>
      ) : (
        "No problems found yet."
      )}
      {first ? (
        <span className="mt-1 block text-bad">
          {seen.cant_look.length === 1 ? "One watch" : `${seen.cant_look.length} watches`} can&apos;t look right now:{" "}
          <Link href={routineHref(first.routine_id)} className="underline">
            {first.routine}
          </Link>{" "}
          ({first.error})
        </span>
      ) : null}
    </p>
  );
}
