"use client";

import { usePathname } from "next/navigation";
import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

import { useApi } from "@/lib/client/api";

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

/** A client of its own: its slug (URLs, IDs, a project's `owner`) and its name as typed. */
export interface Client {
  slug: string;
  name: string;
  projects: number;
  connections: number;
}

interface ScopeState extends Scope {
  set: (next: Scope) => void;
  /** For API calls: `client` and `project`, left out when empty. */
  query: { client?: string; project?: string };
  /** Every client, with or without projects, by name. */
  clients: Client[];
  reloadClients: () => void;
}

const ScopeContext = createContext<ScopeState>({ ...EMPTY, set: () => undefined, query: {}, clients: [], reloadClients: () => undefined });

// Slug -> name, for `place` and `clientName` outside components; kept by ScopeProvider.
const NAMES = new Map<string, string>();

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
  const clients = useApi<Client[]>("clients");
  NAMES.clear();
  for (const client of clients.data ?? []) NAMES.set(client.slug, client.name);

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
  const value = { ...scope, set, query, clients: clients.data ?? [], reloadClients: clients.reload };
  // Pages show clients by name (clientName): wait for the names the first time.
  if (!clients.data && !clients.error) return null;
  return <ScopeContext.Provider value={value}>{children}</ScopeContext.Provider>;
}

export function useScope(): ScopeState {
  return useContext(ScopeContext);
}

/** A client's name as typed, from its slug (or the slug, while clients load). */
export function clientName(slug: string | null | undefined): string {
  return slug ? (NAMES.get(slug) ?? slug) : "";
}

/** "client › project", or what's known of it, with the client's name. */
export function place(client: string | null | undefined, project: string | null | undefined): string {
  if (client && project) return `${clientName(client)} › ${project}`;
  return project ?? clientName(client);
}

/** The slug of a name or slug, as the server matches clients. */
export function clientKey(text: string | null | undefined): string {
  return (text ?? "")
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 63)
    .replace(/-+$/, "");
}

export function sameClient(a: string | null | undefined, b: string | null | undefined): boolean {
  return clientKey(a) === clientKey(b);
}

/** A name that is still its slug ("seaside-windows-doors"): one to offer a proper name for. */
export function looksLikeSlug(client: Client): boolean {
  return client.name === client.slug && /[-]/.test(client.slug);
}
