// Live updates from the app's server: one event stream per window, shared by every view
// that listens (each stream holds a connection open, and a browser allows only six per
// site).
//
// A view never goes quietly stale. EventSource reconnects by itself after a dropped
// connection, but gives up for good on an error answer (a server restarting, too many
// streams): then this opens a new one, waiting longer each time. And whenever events may
// have been missed (the stream came back, the window was hidden or restored from the
// back-forward cache, the network returned), every listener gets a "resync" event, to
// fetch what it shows again.

export interface LiveEvent {
  type: string;
  task_id?: string;
  thread_id?: string;
  decision_id?: string;
  session_id?: string;
  status?: string;
  kind?: string;
  project?: string;
  message_id?: string;
}

export const LIVE_TYPES = ["task", "session_event", "approval", "message", "message_routed"];
export const RESYNC = "resync";
export const STREAM = "/bff/events";
const CLOSED = 2; // EventSource.CLOSED
export const RETRY_MS = [3_000, 6_000, 15_000, 30_000];

type Listener = (event: LiveEvent) => void;

/** What the stream needs from the browser: tests pass fakes. */
export interface Source {
  readyState: number;
  close(): void;
  addEventListener(type: string, listener: (event: { data?: string }) => void): void;
}

export interface Browser {
  open(url: string): Source;
  later(run: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
}

export interface Live {
  subscribe(listener: Listener): () => void;
  /** Tell every listener to fetch again: events may have been missed. */
  resync(): void;
  /** Start over with a new stream (a page restored from the back-forward cache). */
  reopen(): void;
}

export function createLive(browser: Browser): Live {
  const listeners = new Set<Listener>();
  let source: Source | null = null;
  let opened = false; // a stream has opened before: the next open means a gap
  let retries = 0;
  let pending: unknown = null;

  function emit(event: LiveEvent): void {
    for (const listener of [...listeners]) listener(event);
  }

  function dispatch(message: { data?: string }): void {
    let event: LiveEvent;
    try {
      event = JSON.parse(message.data ?? "") as LiveEvent;
    } catch {
      return; // a malformed line
    }
    emit(event);
  }

  function open(): void {
    if (pending !== null) {
      browser.cancel(pending);
      pending = null;
    }
    const mine = browser.open(STREAM);
    source = mine;
    for (const type of LIVE_TYPES) mine.addEventListener(type, dispatch);
    mine.addEventListener("open", () => {
      if (source !== mine) return;
      retries = 0;
      if (opened) emit({ type: RESYNC });
      opened = true;
    });
    mine.addEventListener("error", () => {
      if (source !== mine || mine.readyState !== CLOSED) return; // it reconnects by itself
      mine.close();
      source = null;
      if (listeners.size === 0) return;
      const wait = RETRY_MS[Math.min(retries, RETRY_MS.length - 1)] ?? 30_000;
      retries += 1;
      pending = browser.later(() => {
        pending = null;
        if (source === null && listeners.size > 0) open();
      }, wait);
    });
  }

  function stop(): void {
    if (pending !== null) {
      browser.cancel(pending);
      pending = null;
    }
    source?.close();
    source = null;
  }

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (source === null && pending === null) open();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) {
          stop();
          opened = false; // the next view fetches when it mounts
        }
      };
    },
    resync() {
      if (listeners.size === 0) return;
      if (source === null && pending === null) open();
      emit({ type: RESYNC });
    },
    reopen() {
      if (listeners.size === 0) return;
      stop();
      open();
      emit({ type: RESYNC });
    },
  };
}
