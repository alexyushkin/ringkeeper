import { NextRequest, NextResponse } from "next/server";

import { ApiUnavailableError, callApi, sessionIsValid } from "@/lib/server/api";
import {
  CSRF_COOKIE,
  CSRF_HEADER,
  SESSION_COOKIE,
  clearSession,
  clientAddress,
  setSession,
} from "@/lib/server/cookies";

/**
 * The backend-for-frontend: the browser calls /bff/<path>, this server code
 * calls the API with the admin token and the owner's session. The browser
 * never holds the admin token or the session token (an httpOnly cookie).
 *
 * - Only a few paths work signed out: setup and sign-in.
 * - Every change (non-GET) must come from this site (Origin) and carry the
 *   CSRF token from the rk_csrf cookie in a header (double submit).
 * - Sign-in and setup answers set the session cookie; the token itself is
 *   never sent to the browser.
 */

export const dynamic = "force-dynamic";

const PUBLIC = new Set([
  "GET setup/status",
  "POST setup/owner",
  "POST auth/login",
  "POST auth/passkeys/login/options",
  "POST auth/passkeys/login",
]);
const SIGNS_IN = new Set(["setup/owner", "auth/login", "auth/passkeys/login"]);
const SAFE_PATH = /^[A-Za-z0-9._~-]+(\/[A-Za-z0-9._~%-]+)*$/;

function problem(status: number, detail: string): NextResponse {
  return NextResponse.json({ detail }, { status });
}

function sameOrigin(request: NextRequest): boolean {
  const origin = request.headers.get("origin");
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

async function handle(request: NextRequest, path: string[]): Promise<Response> {
  const joined = path.join("/");
  if (!SAFE_PATH.test(joined) || path.includes("..")) return problem(404, "not found");
  const method = request.method.toUpperCase();
  const session = request.cookies.get(SESSION_COOKIE)?.value;
  const isPublic = PUBLIC.has(`${method} ${joined}`);

  if (method !== "GET" && method !== "HEAD") {
    if (!sameOrigin(request)) return problem(403, "this request didn't come from the app");
    if (!isPublic) {
      const expected = request.cookies.get(CSRF_COOKIE)?.value;
      const sent = request.headers.get(CSRF_HEADER);
      if (!expected || !sent || expected !== sent) {
        return problem(403, "the page is out of date; reload it and try again");
      }
    }
  }
  if (!isPublic && !(await sessionIsValid(session))) {
    const response = problem(401, "sign in first");
    clearSession(request, response);
    return response;
  }

  const streamingUpload = joined === "voice/transcribe";
  let upstream: Response;
  try {
    upstream = await callApi(joined, {
      method,
      search: request.nextUrl.search,
      body: method === "GET" || method === "HEAD" ? null : streamingUpload ? request.body : await request.text(),
      contentType: request.headers.get("content-type"),
      session: isPublic ? null : session,
      client: clientAddress(request.headers),
      userAgent: request.headers.get("user-agent"),
      signal: request.signal,
      duplex: streamingUpload,
    });
  } catch (error) {
    const detail = error instanceof ApiUnavailableError ? error.message : "Something went wrong.";
    return problem(503, detail);
  }

  if (SIGNS_IN.has(joined) && upstream.ok) {
    const { token, expires_at } = (await upstream.json()) as { token: string; expires_at: string };
    const response = NextResponse.json({ ok: true }, { status: upstream.status });
    setSession(request, response, token, new Date(expires_at));
    return response;
  }
  if (joined === "auth/logout") {
    const response = new NextResponse(null, { status: 204 });
    clearSession(request, response);
    return response;
  }

  const headers = new Headers();
  for (const name of ["content-type", "content-disposition", "retry-after", "cache-control"]) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  if (joined === "events") headers.set("X-Accel-Buffering", "no");
  return new Response(upstream.status === 204 ? null : upstream.body, {
    status: upstream.status,
    headers,
  });
}

type Context = { params: Promise<{ path: string[] }> };

export async function GET(request: NextRequest, context: Context): Promise<Response> {
  return handle(request, (await context.params).path);
}
export async function POST(request: NextRequest, context: Context): Promise<Response> {
  return handle(request, (await context.params).path);
}
export async function PUT(request: NextRequest, context: Context): Promise<Response> {
  return handle(request, (await context.params).path);
}
export async function PATCH(request: NextRequest, context: Context): Promise<Response> {
  return handle(request, (await context.params).path);
}
export async function DELETE(request: NextRequest, context: Context): Promise<Response> {
  return handle(request, (await context.params).path);
}
