// The app's own history: where Back and Forward can go, and what each page looked like.
//
// A browser doesn't say whether there's anywhere to go back or forward to, so each history
// entry gets an index (kept beside Next's own state in history.state), and the furthest one
// reached is remembered for the tab's session. Each entry keeps its scroll position and which
// folded details were open, to put back on Back or Forward. A dialog that's open takes Back
// for itself: it closes, and the page stays.

export interface Place {
  index: number;
  furthest: number;
}

export interface Win {
  history: {
    state: unknown;
    pushState(data: unknown, unused: string, url?: string | URL | null): void;
    replaceState(data: unknown, unused: string, url?: string | URL | null): void;
    back(): void;
    forward(): void;
    scrollRestoration?: string;
  };
  sessionStorage: { getItem(key: string): string | null; setItem(key: string, value: string): void };
  addEventListener(type: string, listener: (event: { state?: unknown }) => void): void;
  scrollY: number;
  scrollTo(x: number, y: number): void;
  /** How far the page can scroll now (it grows as data loads). */
  scrollable(): number;
  later(run: () => void, ms: number): unknown;
}

const STORE = "rk-history";
const RESTORE_FOR_MS = 3_000; // a page's data may take this long to be tall enough again
// Once there, it's held there a moment: a page that scrolls itself as it loads (to a section
// its address names) would move it. Scrolling yourself ends it at once.
const HOLD_MS = 1_500;

interface Stored {
  furthest: number;
  scroll: Record<string, number>;
  open: Record<string, Record<string, boolean>>;
}

function indexOf(state: unknown): number | null {
  const found = (state as { rkIndex?: unknown } | null)?.rkIndex;
  return typeof found === "number" ? found : null;
}

export interface AppHistory {
  place(): Place;
  subscribe(listener: () => void): () => void;
  back(): void;
  forward(): void;
  /** A dialog that's open: Back closes it instead of leaving the page. */
  holdBack(close: () => void): () => void;
  /** Whether a folded detail on this entry was open (null: not known). */
  wasOpen(key: string): boolean | null;
  setOpen(key: string, open: boolean): void;
}

export function createHistory(win: Win): AppHistory {
  const listeners = new Set<() => void>();
  const closers: (() => void)[] = [];
  let stored: Stored = { furthest: 0, scroll: {}, open: {} };
  try {
    stored = { ...stored, ...(JSON.parse(win.sessionStorage.getItem(STORE) ?? "{}") as Partial<Stored>) };
  } catch {
    // a fresh start
  }
  const save = () => {
    try {
      win.sessionStorage.setItem(STORE, JSON.stringify(stored));
    } catch {
      // blocked storage: Back still works, the page just isn't put back as it was
    }
  };
  const emit = () => {
    for (const listener of [...listeners]) listener();
  };

  let index = indexOf(win.history.state) ?? 0;
  const push = win.history.pushState.bind(win.history);
  const replace = win.history.replaceState.bind(win.history);
  const mark = (data: unknown, at: number) => ({ ...((data as object | null) ?? {}), rkIndex: at });
  if (indexOf(win.history.state) === null) {
    // The app's first page in this tab: nothing before it is the app's, and nothing kept
    // for an earlier run of it here applies.
    stored = { furthest: 0, scroll: {}, open: {} };
    replace(mark(win.history.state, 0), "");
  }
  // The state of the page shown now, Next's included: an entry the browser made for a link to
  // #somewhere on it gets a copy, or Next would reload the page on Back to it.
  let shown: unknown = win.history.state;
  stored.furthest = Math.max(stored.furthest, index);
  win.history.scrollRestoration = "manual"; // each entry's scroll is put back here
  save();

  win.history.pushState = (data, unused, url) => {
    stored.scroll[String(index)] = win.scrollY;
    index += 1;
    stored.furthest = index; // a new step: what was ahead is gone
    for (const key of Object.keys(stored.open)) if (Number(key) >= index) delete stored.open[key];
    save();
    push(mark(data, index), unused, url);
    shown = data;
    emit();
  };
  win.history.replaceState = (data, unused, url) => {
    replace(mark(data, indexOf(data) ?? index), unused, url);
    shown = data;
  };

  let restoring = 0;
  let moved = false; // the reader scrolled, clicked or typed since
  for (const type of ["wheel", "touchstart", "keydown", "mousedown"]) {
    win.addEventListener(type, () => {
      moved = true;
    });
  }
  function restore(y: number) {
    const mine = ++restoring;
    const started = Date.now();
    let reached: number | null = null;
    moved = false;
    const attempt = () => {
      if (mine !== restoring || moved) return; // another move since, or the reader's own
      const now = Date.now();
      if (reached === null && win.scrollable() < y && now - started < RESTORE_FOR_MS) {
        win.scrollTo(0, win.scrollable()); // as far as it goes while the page fills in
        win.later(attempt, 50);
        return;
      }
      reached ??= now;
      if (win.scrollY !== y) win.scrollTo(0, y);
      if (now - reached < HOLD_MS) win.later(attempt, 100);
    };
    attempt();
  }
  // The entry shown now. history.state is right from the moment a page changes, even when
  // the page renders before this hears of it.
  const here = () => String(indexOf(win.history.state) ?? index);

  // A step the app didn't take itself (a link to #somewhere on the page): a new entry with
  // no index yet. Browsers say so with hashchange, some with popstate too.
  const unmarked = (state: unknown) => {
    index += 1;
    stored.furthest = index;
    replace(mark(state ?? shown, index), "");
    save();
    emit();
  };
  win.addEventListener("hashchange", () => {
    if (indexOf(win.history.state) === null) {
      stored.scroll[String(index)] = win.scrollY;
      unmarked(win.history.state);
    }
  });
  win.addEventListener("popstate", (event) => {
    stored.scroll[String(index)] = win.scrollY; // the page being left
    const found = indexOf(event.state);
    if (found === null) return unmarked(event.state);
    index = found;
    shown = event.state;
    stored.furthest = Math.max(stored.furthest, index);
    save();
    emit();
    restore(stored.scroll[String(index)] ?? 0);
  });

  return {
    place: () => ({ index, furthest: stored.furthest }),
    subscribe(listener) {
      listeners.add(listener);
      return () => listeners.delete(listener);
    },
    back() {
      const close = closers.pop();
      if (close) return close();
      if (index > 0) win.history.back();
    },
    forward() {
      if (closers.length) return; // a dialog is open: it stays where it is
      if (index < stored.furthest) win.history.forward();
    },
    holdBack(close) {
      closers.push(close);
      return () => {
        const at = closers.lastIndexOf(close);
        if (at >= 0) closers.splice(at, 1);
      };
    },
    wasOpen(key) {
      const found = stored.open[here()]?.[key];
      return found === undefined ? null : found;
    },
    setOpen(key, open) {
      (stored.open[here()] ??= {})[key] = open;
      save();
    },
  };
}
