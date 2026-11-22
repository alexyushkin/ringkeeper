/** Where a finished task's result goes in a conversation. */

interface Sent {
  id: string;
  created_at: string;
}

interface Finished {
  created_at: string;
  finished_at: string | null;
}

/** Which message each finished task's result follows: the last one sent before it finished,
 * so a follow-up sent after it comes after it. One that finished before every message follows
 * the first. */
export function resultsAfter<T extends Finished>(messages: Sent[], finished: T[]): Map<string, T[]> {
  const after = new Map<string, T[]>();
  if (!messages.length) return after;
  for (const task of finished) {
    const at = new Date(task.finished_at ?? task.created_at).getTime();
    let last = messages[0]!;
    for (const message of messages) if (new Date(message.created_at).getTime() <= at) last = message;
    after.set(last.id, [...(after.get(last.id) ?? []), task]);
  }
  return after;
}
