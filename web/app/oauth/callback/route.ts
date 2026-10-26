import { NextRequest, NextResponse } from "next/server";

import { HOST_REFUSED, hostAllowed } from "@/lib/hosts";
import { ApiUnavailableError, callApi } from "@/lib/server/api";

/**
 * Where a service's sign-in page sends the browser back (server mode; the
 * desktop backend answers this path itself). No session is needed: the
 * one-time state in the query is the check, and the API makes the connection.
 */

export const dynamic = "force-dynamic";

export async function GET(request: NextRequest): Promise<Response> {
  if (!hostAllowed(request.headers)) return new NextResponse(HOST_REFUSED, { status: 421 });
  try {
    const answer = await callApi("oauth/callback", { search: request.nextUrl.search });
    return new NextResponse(await answer.text(), {
      status: answer.status,
      headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
    });
  } catch (error) {
    const detail = error instanceof ApiUnavailableError ? error.message : "Something went wrong.";
    return new NextResponse(detail, { status: 503 });
  }
}
