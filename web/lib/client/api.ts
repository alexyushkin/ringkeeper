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

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|;\s*)rk_csrf=([^;]+)/);
  return match ? decodeURIComponent(match[1] ?? "") : "";
}

function detail(body: unknown, status: number): string {
  if (body && typeof body === "object" && "detail" in body) {
    const value = (body as { detail: unknown }).detail;
    if (typeof value === "string") return value;
    if (Array.isArray(value)) {
      // FastAPI validation errors: "field: message"
      return value
        .map((item: { loc?: unknown[]; msg?: string }) =>
          [item.loc?.filter((p) => p !== "body").join("."), item.msg].filter(Boolean).join(": "),
        )
        .join("; ");
    }
  }
  return status >= 500 ? "Something went wrong on the server. Try again." : `Request failed (${status})`;
}

export interface Options {
  method?: string;
  json?: unknown;
  body?: BodyInit;
  contentType?: string;
  query?: Record<string, string | number | boolean | undefined | null>;
}

export async function api<T = unknown>(path: string, options: Options = {}): Promise<T> {
  const method = options.method ?? (options.json !== undefined || options.body ? "POST" : "GET");
  const headers: Record<string, string> = {};
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
  const response = await fetch(url, { method, headers, body, credentials: "same-origin" });
  if (response.status === 401 && !path.startsWith("auth/") && !path.startsWith("setup/")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
    throw new ApiError(401, "Please sign in again.");
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const parsed: unknown = text ? JSON.parse(text) : undefined;
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

/** Live updates (server-sent events) from the app's server; reconnects by itself. */
export function useLive(onEvent: (event: LiveEvent) => void, enabled = true): void {
  const handler = useRef(onEvent);
  handler.current = onEvent;
  useEffect(() => {
    if (!enabled) return;
    const source = new EventSource("/bff/events");
    const listener = (message: MessageEvent<string>) => {
      try {
        handler.current(JSON.parse(message.data) as LiveEvent);
      } catch {
        /* ignore a malformed line */
      }
    };
    for (const type of LIVE_TYPES) source.addEventListener(type, listener as EventListener);
    return () => source.close();
  }, [enabled]);
}
