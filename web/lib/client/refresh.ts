// How a screen learns that work finished in the background, in one place: which live events
// change what each of the app's reads returns. useApi and usePaged look a read up here: a
// read in this table is fetched again when one of its events arrives, and whenever events
// may have been missed (the stream came back, the window was hidden). So a screen shows
// a finished task, a backup or an upload without a reload, and without a listener of its own.
//
// The events: "task", "approval", "message" and "message_routed" come from the database as
// those rows change; "job:<kind>" from background work that isn't one of them
// (services/jobs.py: the weekly backup, a Google Drive upload, the daily connection check).
//
// Screens that listen for more than this, by id, keep a listener of their own: a task's page
// (its steps as they happen) and a conversation's page (its replies, and every few seconds
// while one is on its way).
//
// Screens that poll do so only while something they started is going on, and stop when it
// ends: a sign-in in the browser (Connect a service, Google, a vendor's app), downloading
// the scan reader, voice typing or Docker's image, the desktop update check, Check my
// setup, and linking a Telegram chat.

// Self-contained, so `node --test` reads it as it is: live.ts's resync event and event shape.
const RESYNC = "resync";
interface LiveEvent {
  type: string;
  kind?: string;
}

export interface Watch {
  page: string; // where it shows
  read: string; // the API path, "*" for one part of it (an id)
  on: string[]; // the events that change it
}

export const WATCHES: Watch[] = [
  { page: "Home: recent conversations", read: "threads", on: ["message", "message_routed", "task", "approval"] },
  { page: "Home: first steps, and the backup notices", read: "start", on: ["task", "message_routed", "job:backup", "job:backup_upload"] },
  { page: "Home: what happened yesterday", read: "digest", on: ["task", "approval"] },
  { page: "Home: sent for later", read: "scheduled", on: ["message", "message_routed"] },
  { page: "Home: interrupted tasks", read: "tasks/interrupted", on: ["task"] },
  { page: "Home and a project: what's being watched", read: "watching", on: ["task"] },
  { page: "Tasks, and a client's or project's tasks", read: "tasks", on: ["task"] },
  { page: "Tasks: old failed ones to archive", read: "cleanup/old-tasks", on: ["task"] },
  { page: "Approvals, and the count beside it in the menu", read: "decisions", on: ["approval"] },
  { page: "Approvals: trusted for an hour", read: "trusts", on: ["approval"] },
  { page: "A task's changes", read: "tasks/*/changes", on: ["approval", "task"] },
  { page: "A conversation's changes", read: "threads/*/changes", on: ["approval", "task"] },
  { page: "A pass's changes", read: "passes/*/changes", on: ["approval", "task"] },
  { page: "A routine's runs", read: "routines/*/runs", on: ["task"] },
  { page: "Settings: Backup, and Google Drive", read: "desktop/backup", on: ["job:backup", "job:backup_upload"] },
  { page: "Connections: whether each one works", read: "connections", on: ["job:health"] },
];

/** "job:backup" for a background job's event, the type for the rest. */
export function eventName(event: LiveEvent): string {
  return event.type === "job" ? `job:${event.kind ?? ""}` : event.type;
}

function fits(pattern: string, path: string): boolean {
  const want = pattern.split("/");
  const got = path.split("?")[0]?.split("/") ?? [];
  return want.length === got.length && want.every((part, i) => part === "*" || part === got[i]);
}

/** The table's row for a read (none: it isn't fetched again by itself). */
export function watchOf(path: string | null): Watch | undefined {
  return path === null ? undefined : WATCHES.find((w) => fits(w.read, path));
}

/** Whether this event means the read should be fetched again. */
export function changes(path: string | null, event: LiveEvent): boolean {
  const watch = watchOf(path);
  if (watch === undefined) return false;
  return event.type === RESYNC || watch.on.includes(eventName(event));
}
