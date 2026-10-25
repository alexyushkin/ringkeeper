import "server-only";

import { readFileSync } from "node:fs";

/**
 * The web app's server side talks to Ringkeeper's API with the admin token.
 * The browser never sees it: the token is read here, from the file the api
 * writes into a volume only the two services mount (RINGKEEPER_API_TOKEN_FILE),
 * or from RINGKEEPER_API_TOKEN in development.
 */

const API_URL = (process.env.RINGKEEPER_API_URL ?? "http://api:8000").replace(/\/$/, "");
const TOKEN_FILE = process.env.RINGKEEPER_API_TOKEN_FILE ?? "/run/ringkeeper-web/api-token";

export const SESSION_HEADER = "X-Ringkeeper-Session";
export const CLIENT_HEADER = "X-Ringkeeper-Client";

function adminToken(): string {
  const fromEnv = process.env.RINGKEEPER_API_TOKEN;
  if (fromEnv) return fromEnv.trim();
  try {
    // Read each time: a rotated token (ringkeeper auth api-token --rotate) works at once.
    return readFileSync(TOKEN_FILE, "utf8").trim();
  } catch {
    throw new ApiUnavailableError("The web app can't find Ringkeeper's API token yet.");
  }
}

export class ApiUnavailableError extends Error {}

export interface CallOptions {
  method?: string;
  body?: BodyInit | null;
  contentType?: string | null;
  session?: string | null;
  client?: string | null;
  userAgent?: string | null;
  search?: string;
  signal?: AbortSignal;
  duplex?: boolean;
}

/** One call to the API. Errors come back as responses (the caller passes them on). */
export async function callApi(path: string, options: CallOptions = {}): Promise<Response> {
  const headers = new Headers({ Authorization: `Bearer ${adminToken()}` });
  if (options.contentType) headers.set("Content-Type", options.contentType);
  if (options.session) headers.set(SESSION_HEADER, options.session);
  if (options.client) headers.set(CLIENT_HEADER, options.client);
  if (options.userAgent) headers.set("User-Agent", options.userAgent.slice(0, 300));
  const init: RequestInit & { duplex?: "half" } = {
    method: options.method ?? "GET",
    headers,
    body: options.body ?? null,
    cache: "no-store",
    redirect: "manual",
    signal: options.signal,
  };
  if (options.duplex) init.duplex = "half"; // streaming request bodies (voice uploads)
  try {
    return await fetch(`${API_URL}/${path.replace(/^\//, "")}${options.search ?? ""}`, init);
  } catch (error) {
    if (error instanceof ApiUnavailableError) throw error;
    throw new ApiUnavailableError("Ringkeeper's API isn't reachable right now.");
  }
}

/** Is this owner session still valid? (Checked on every request through the app.) */
export async function sessionIsValid(session: string | undefined): Promise<boolean> {
  if (!session) return false;
  try {
    const response = await callApi("auth/session", { session });
    return response.ok;
  } catch {
    return false;
  }
}
