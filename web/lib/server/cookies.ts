import "server-only";

import { randomBytes } from "node:crypto";
import type { NextRequest, NextResponse } from "next/server";

/**
 * The browser's session cookie holds the owner session token: httpOnly (no
 * script can read it), SameSite=Strict (never sent from another site), and
 * Secure whenever the request came over HTTPS (directly, or through a proxy
 * like Tailscale Serve that says so in X-Forwarded-Proto). On plain
 * http://localhost it can't be Secure: some browsers (Safari) would drop it.
 * A second, readable cookie holds the CSRF token that every change echoes.
 */

export const SESSION_COOKIE = "rk_session";
export const CSRF_COOKIE = "rk_csrf";
export const CSRF_HEADER = "x-csrf-token";

export function secureCookies(request: NextRequest): boolean {
  if (process.env.RINGKEEPER_WEB_INSECURE_COOKIES === "1") return false;
  const forwarded = request.headers.get("x-forwarded-proto")?.split(",")[0]?.trim();
  if ((forwarded ?? request.nextUrl.protocol.replace(":", "")) === "https") return true;
  // Reached through the configured HTTPS address (e.g. Tailscale Serve), even if
  // the proxy doesn't say so.
  const origin = process.env.RINGKEEPER_WEB_ORIGIN ?? "";
  const host = request.headers.get("x-forwarded-host") ?? request.headers.get("host") ?? "";
  return origin.startsWith("https://") && host !== "" && new URL(origin).host === host;
}

export function setSession(request: NextRequest, response: NextResponse, token: string, expires: Date): void {
  const secure = secureCookies(request);
  response.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure,
    sameSite: "strict",
    path: "/",
    expires,
  });
  response.cookies.set(CSRF_COOKIE, randomBytes(24).toString("base64url"), {
    httpOnly: false,
    secure,
    sameSite: "strict",
    path: "/",
    expires,
  });
}

export function clearSession(request: NextRequest, response: NextResponse): void {
  for (const name of [SESSION_COOKIE, CSRF_COOKIE]) {
    response.cookies.set(name, "", { path: "/", expires: new Date(0), secure: secureCookies(request) });
  }
}

/**
 * Where the request came from, for sign-in rate limits. Proxies are trusted
 * only if told so (RINGKEEPER_WEB_TRUST_PROXY=1), and then only the address
 * the proxy itself added: the last X-Forwarded-For entry. Earlier entries
 * come from the browser and could be anything. Without a trusted proxy every
 * browser is "direct" (paced together, never blocked one by one).
 */
export function clientAddress(headers: Headers): string {
  if (process.env.RINGKEEPER_WEB_TRUST_PROXY === "1") {
    const hops = (headers.get("x-forwarded-for") ?? "").split(",").map((hop) => hop.trim());
    const last = hops.at(-1);
    if (last && /^[0-9A-Fa-f.:]{2,45}$/.test(last)) return last;
  }
  return "direct";
}
