// What a conversation shows under a request the router hasn't placed yet.
export const FINDING = "Finding the right project…";
export const STILL = "Still working out where this goes…";
export const STILL_AFTER_MS = 20_000;

/** The line to show for a request sent at `sentAt`, and how long until it changes (null: it won't). */
export function routingLine(sentAt: string, now: number): { text: string; changesIn: number | null } {
  const waited = now - new Date(sentAt).getTime();
  if (Number.isNaN(waited) || waited >= STILL_AFTER_MS) return { text: STILL, changesIn: null };
  return { text: FINDING, changesIn: STILL_AFTER_MS - waited };
}
