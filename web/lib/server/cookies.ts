import "server-only";

import { randomBytes } from "node:crypto";
import type { NextResponse } from "next/server";

/**
 * The browser's session cookie holds the owner session token: httpOnly (no
 * script can read it), SameSite=Strict (never sent from another site), and
 * Secure whenever the app is served over HTTPS. A second, readable cookie
 * holds the CSRF token that every change must echo in a header.
 */

export const SESSION_COOKIE = "rk_session";
export const CSRF_COOKIE = "rk_csrf";
export const CSRF_HEADER = "x-csrf-token";

export function secureCookies(): boolean {
  // Off only for plain-http development on localhost.
  return process.env.RINGKEEPER_WEB_INSECURE_COOKIES !== "1";
}

export function setSession(response: NextResponse, token: string, expires: Date): void {
  response.cookies.set(SESSION_COOKIE, token, {
    httpOnly: true,
    secure: secureCookies(),
    sameSite: "strict",
    path: "/",
    expires,
  });
  response.cookies.set(CSRF_COOKIE, randomBytes(24).toString("base64url"), {
    httpOnly: false,
    secure: secureCookies(),
    sameSite: "strict",
    path: "/",
    expires,
  });
}

export function clearSession(response: NextResponse): void {
  for (const name of [SESSION_COOKIE, CSRF_COOKIE]) {
    response.cookies.set(name, "", { path: "/", expires: new Date(0), secure: secureCookies() });
  }
}

/** Where the request came from, for sign-in rate limits. Proxies are trusted only if told so. */
export function clientAddress(headers: Headers): string {
  if (process.env.RINGKEEPER_WEB_TRUST_PROXY === "1") {
    const forwarded = headers.get("x-forwarded-for")?.split(",")[0]?.trim();
    if (forwarded) return forwarded;
  }
  return "direct";
}
