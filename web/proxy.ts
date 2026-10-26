import { NextRequest, NextResponse } from "next/server";

import { HOST_REFUSED, hostAllowed } from "@/lib/hosts";

/**
 * Runs before every page: a strict Content-Security-Policy with a fresh nonce
 * (no inline scripts or styles without it, nothing from other sites), and a
 * redirect to sign-in for pages that need a session. The BFF (/bff) checks
 * sessions itself, against the API.
 */

const OPEN_PAGES = ["/login", "/setup", "/oauth/callback"];

export function proxy(request: NextRequest): NextResponse {
  if (!hostAllowed(request.headers)) {
    return new NextResponse(HOST_REFUSED, { status: 421, headers: { "Content-Type": "text/plain; charset=utf-8" } });
  }
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const dev = process.env.NODE_ENV === "development";
  const csp = [
    "default-src 'self'",
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic'${dev ? " 'unsafe-eval'" : ""}`,
    `style-src 'self' 'nonce-${nonce}'`,
    "img-src 'self' blob: data:",
    "font-src 'self'",
    "connect-src 'self'",
    "media-src 'self' blob:",
    "worker-src 'self'",
    "manifest-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    ...(dev ? [] : ["upgrade-insecure-requests"]),
  ].join("; ");

  const path = request.nextUrl.pathname;
  const signedIn = Boolean(request.cookies.get("rk_session")?.value);
  if (!signedIn && !OPEN_PAGES.some((p) => path === p || path.startsWith(`${p}/`))) {
    const login = new URL("/login", request.url);
    if (path !== "/") login.searchParams.set("next", path);
    return NextResponse.redirect(login);
  }

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);
  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    {
      source: "/((?!bff|_next/static|_next/image|favicon.ico|icon|apple-icon|manifest.webmanifest|sw.js).*)",
      missing: [
        { type: "header", key: "next-router-prefetch" },
        { type: "header", key: "purpose", value: "prefetch" },
      ],
    },
  ],
};
