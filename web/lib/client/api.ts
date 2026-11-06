"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/** Calls go to the app's own server (/bff), never straight to Ringkeeper's API. */

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: string | null,
  ) {
    super(message);
  }
}

export const BACKEND_DOWN =
  "Ringkeeper isn't responding. Wait a few seconds. If it doesn't come back, right-click the tray icon, choose Quit, and open Ringkeeper again.";

// A form field's name as the form calls it, for "Address can't be empty".
const FIELDS: Record<string, string> = {
  base_url: "The address",
  name: "The name",
  secret: "The key",
  owner: "The client",
  workspace_path: "The folder",
  instructions: "The instructions",
  prompt: "The instructions",
  body: "The message",
  password: "The password",
};

function field(loc: unknown[] | undefined): string {
  const key = String(loc?.filter((p) => p !== "body" && typeof p === "string").at(-1) ?? "");
  if (!key) return "Something you entered";
  return FIELDS[key] ?? `The ${key.replace(/_/g, " ")}`;
}

function problem(msg: string): string {
  if (/at least 1 (character|item)/i.test(msg) || /field required/i.test(msg)) return "can't be empty";
  if (/should match pattern/i.test(msg)) return "has characters that aren't allowed (use letters, digits and dashes)";
  if (/at most \d+/i.test(msg)) return "is too long";
  if (/valid (url|integer|number)/i.test(msg)) return "doesn't look right";
  return `isn't right (${msg.toLowerCase()})`;
}

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)rk_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1] ?? "") : "";
}

function detail(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const value = (body as { detail: unknown }).detail;
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      // FastAPI validation errors: "The address can't be empty."
      return value.map((item: { loc?: unknown[]; msg?: string }) => `${field(item.loc)} ${problem(item.msg ?? "")}.`).join(" ");
    }
  }
  if (status === 502 || status === 504) return BACKEND_DOWN;
  if (status >= 500) return "Something went wrong inside Ringkeeper. Try again; if it keeps happening, use Report a problem on the Help page.";
  console.warn(`Ringkeeper answered ${status}`);
  return "That didn't work. Refresh the page and try again.";
}

export interface Options {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  contentType?: string;
  headers?: Record<string, string>;
  query?: Record<string, string | number | boolean | undefined | null>;
}

type ConfirmHandler = () => Promise<boolean>;
let confirmHandler: ConfirmHandler | null = null;

/** The password dialog (ConfirmPassword) registers itself here. */
export function setConfirmHandler(handler: ConfirmHandler | null): void {
  confirmHandler = handler;
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const response = await send(path, options);
  // A sensitive change after a while: confirm the password, then try once more.
  if (response.status === 403 && response.headers.get("x-ringkeeper-confirm") === "password" && confirmHandler) {
    if (await confirmHandler()) return read<T>(path, await send(path, options));
    throw new ApiError(403, "Cancelled: this change needs your password. Try again and enter it.");
  }
  return read<T>(path, response);
}

async function send(path: string, options: Options): Promise<Response> {
  const method = options.method ?? (options.json !== undefined || options.body ? "POST" : "GET");
  const headers: Record<string, string> = { ...(options.headers ?? {}) };
  if (method !== "GET") headers["x-csrf-token"] = csrfToken();
  let body: BodyInit | undefined = options.body;
  if (options.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.json);
  } else if (options.contentType) {
    headers["Content-Type"] = options.contentType;
  }
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(options.query ?? {})) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const url = `/bff/${path.replace(/^\//, "")}${search.size ? `?${search}` : ""}`;
  try {
    return await fetch(url, { method, headers, body, credentials: "same-origin" });
  } catch {
    // The browser's own "Failed to fetch": nothing answered at all.
    throw new ApiError(0, BACKEND_DOWN);
  }
}

function parse(text: string): unknown {
  try {
    return text ? JSON.parse(text) : undefined;
  } catch {
    return undefined; // e.g. a proxy's HTML error page
  }
}

async function read<T>(path: string, response: Response): Promise<T> {
  if (response.status === 401 && !path.startsWith("auth/") && !path.startsWith("setup/")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new ApiError(401, "Please sign in again.");
  }
  if (response.status === 204) return undefined as T;
  const parsed = parse(await response.text());
  if (!response.ok) {
    throw new ApiError(response.status, detail(parsed, response.status), response.headers.get("retry-after"));
  }
  return parsed as T;
}

export interface Loaded<T> {
  data: T | undefined;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

/** GET a path; reloads when `reload()` is called or a live event says so. */
export function useApi<T>(path: string | null, query?: Options["query"]): Loaded<T> {
  const [data, setData] = useState<T | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(query ?? {});
  useEffect(() => {
    if (path === null) return;
    let cancelled = false;
    setLoading(true);
    api<T>(path, { query: JSON.parse(key) as Options["query"] })
      .then((result) => {
        if (!cancelled) {
          setData(result);
          setError(null);
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [path, key, tick]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  return { data, error, loading, reload };
}

export const PAGE_SIZE = 50;
const MOST_PER_PAGE = 500;

export interface PagedList<T> {
  items: T[] | undefined;
  total: number | undefined; // in the whole list, not just what's loaded
  more: boolean;
  error: string | null;
  loading: boolean;
  loadingMore: boolean;
  reload: () => void;
  loadMore: () => void;
}

interface OnePage<T> {
  items: T[];
  next: string | null;
  total: number | undefined;
}

async function onePage<T>(path: string, query: Options["query"], limit: number, cursor: string | null): Promise<OnePage<T>> {
  const options: Options = { query: { ...query, limit, cursor } };
  let response = await send(path, options);
  if (response.status === 403 && response.headers.get("x-ringkeeper-confirm") === "password" && confirmHandler && (await confirmHandler())) {
    response = await send(path, options);
  }
  const next = response.headers.get("x-next-cursor");
  const total = response.headers.get("x-total-count");
  return { items: (await read<T[]>(path, response)) ?? [], next, total: total === null ? undefined : Number(total) };
}

function same<T>(item: T): unknown {
  return (item as { id?: unknown }).id ?? item;
}

/** A long list, a page at a time. A reload (a live event, say) keeps as many rows as are loaded. */
export function usePaged<T>(path: string | null, query?: Options["query"], size: number = PAGE_SIZE): PagedList<T> {
  const [items, setItems] = useState<T[] | undefined>(undefined);
  const [total, setTotal] = useState<number | undefined>(undefined);
  const [next, setNext] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [tick, setTick] = useState(0);
  const key = JSON.stringify(query ?? {});
  const shown = useRef({ key, count: 0 });
  const latest = useRef(0); // only the newest request's answer counts
  useEffect(() => {
    if (path === null) return;
    const mine = ++latest.current;
    const fresh = shown.current.key !== key;
    const want = fresh ? size : Math.max(size, shown.current.count);
    shown.current.key = key;
    setLoading(true);
    void (async () => {
      try {
        const asked = JSON.parse(key) as Options["query"];
        const rows: T[] = [];
        let cursor: string | null = null;
        let count: number | undefined;
        do {
          const page: OnePage<T> = await onePage<T>(path, asked, Math.min(want - rows.length, MOST_PER_PAGE), cursor);
          rows.push(...page.items);
          cursor = page.next;
          count = page.total;
        } while (cursor && rows.length < want);
        if (mine !== latest.current) return;
        shown.current.count = rows.length;
        setItems(rows);
        setTotal(count);
        setNext(cursor);
        setError(null);
      } catch (err) {
        if (mine === latest.current) setError(err instanceof Error ? err.message : String(err));
      } finally {
        if (mine === latest.current) setLoading(false);
      }
    })();
  }, [path, key, tick, size]);
  const reload = useCallback(() => setTick((n) => n + 1), []);
  const loadMore = useCallback(() => {
    if (path === null || !next) return;
    const mine = ++latest.current;
    setLoadingMore(true);
    void onePage<T>(path, JSON.parse(key) as Options["query"], size, next)
      .then((page) => {
        if (mine !== latest.current) return;
        setItems((now) => {
          const known = new Set((now ?? []).map(same));
          const all = [...(now ?? []), ...page.items.filter((item) => !known.has(same(item)))];
          shown.current.count = all.length;
          return all;
        });
        setTotal(page.total);
        setNext(page.next);
        setError(null);
      })
      .catch((err: unknown) => {
        if (mine === latest.current) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => setLoadingMore(false));
  }, [path, key, next, size]);
  return { items, total, more: next !== null, error, loading, loadingMore, reload, loadMore };
}

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

const LIVE_TYPES = ["task", "session_event", "approval", "message", "message_routed"];

// One stream per window, shared by every component that listens: each stream
// holds a connection open, and a browser allows only six per site.
type Listener = (event: LiveEvent) => void;
const listeners = new Set<Listener>();
let source: EventSource | null = null;

function dispatch(message: MessageEvent<string>): void {
  let event: LiveEvent;
  try {
    event = JSON.parse(message.data) as LiveEvent;
  } catch {
    return; // a malformed line
  }
  for (const listener of [...listeners]) listener(event);
}

function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  if (source === null) {
    source = new EventSource("/bff/events");
    for (const type of LIVE_TYPES) source.addEventListener(type, dispatch as EventListener);
  }
  return () => {
    listeners.delete(listener);
    if (listeners.size === 0 && source !== null) {
      source.close();
      source = null;
    }
  };
}

/** Live updates (server-sent events) from the app's server; reconnects by itself. */
export function useLive(onEvent: (event: LiveEvent) => void, enabled = true): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (!enabled) return;
    return subscribe((event) => handler.current(event));
  }, [enabled]);
}
