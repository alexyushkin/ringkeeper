// What a conversation shows under a request the router hasn't answered yet: never silence.
export const ROUTING = "Routing…";
export const NO_REPLY = "No reply after 10 seconds.";
export const NO_REPLY_AFTER_MS = 10_000;

/** The line to show for a request sent at `sentAt`, whether it offers Retry, and how long
 * until it changes (null: it won't). */
export function routingLine(sentAt: string, now: number): { text: string; retry: boolean; changesIn: number | null } {
  const waited = now - new Date(sentAt).getTime();
  if (Number.isNaN(waited) || waited >= NO_REPLY_AFTER_MS) return { text: NO_REPLY, retry: true, changesIn: null };
  return { text: ROUTING, retry: false, changesIn: NO_REPLY_AFTER_MS - waited };
}
