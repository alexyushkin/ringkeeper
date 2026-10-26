"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

/**
 * The client/project switcher's choice: which client (and maybe project) the
 * lists show. Remembered in this browser and kept in the URL (?client=&project=),
 * so a link or a reload shows the same view. Empty: everything.
 */

export interface Scope {
  client: string;
  project: string;
}

const KEY = "rk-scope";
const EMPTY: Scope = { client: "", project: "" };

interface ScopeState extends Scope {
  set: (next: Scope) => void;
  /** For API calls: `client` and `project`, left out when empty. */
  query: { client?: string; project?: string };
}

const ScopeContext = createContext<ScopeState>({ ...EMPTY, set: () => undefined, query: {} });

function fromUrl(): Scope | null {
  const params = new URLSearchParams(window.location.search);
  if (!params.has("client") && !params.has("project")) return null;
  return { client: params.get("client") ?? "", project: params.get("project") ?? "" };
}

function remembered(): Scope {
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as Partial<Scope> | null;
    return { client: saved?.client ?? "", project: saved?.project ?? "" };
  } catch {
    return EMPTY;
  }
}

function writeUrl(scope: Scope): void {
  const url = new URL(window.location.href);
  for (const key of ["client", "project"] as const) {
    if (scope[key]) url.searchParams.set(key, scope[key]);
    else url.searchParams.delete(key);
  }
  if (url.href !== window.location.href) window.history.replaceState(window.history.state, "", url);
}

export function ScopeProvider({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [scope, setScope] = useState<Scope>(EMPTY);

  // The URL wins (a shared link), then what this browser remembers.
  useEffect(() => {
    setScope(fromUrl() ?? remembered());
  }, []);
  // Each page's URL carries the choice too.
  useEffect(() => {
    writeUrl(scope);
  }, [scope, path]);

  const set = useCallback((next: Scope) => {
    setScope(next);
    try {
      localStorage.setItem(KEY, JSON.stringify(next));
    } catch {
      /* private mode: it just isn't remembered */
    }
  }, []);

  const query = { ...(scope.client ? { client: scope.client } : {}), ...(scope.project ? { project: scope.project } : {}) };
  return <ScopeContext.Provider value={{ ...scope, set, query }}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeState {
  return useContext(ScopeContext);
}

/** "client › project", or what's known of it. */
export function place(client: string | null | undefined, project: string | null | undefined): string {
  if (client && project) return `${client} › ${project}`;
  return project ?? client ?? "";
}

export function sameClient(a: string | null | undefined, b: string | null | undefined): boolean {
  return (a ?? "").trim().toLowerCase() === (b ?? "").trim().toLowerCase();
}
