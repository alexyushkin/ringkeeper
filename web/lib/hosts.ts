/**
 * Which host names the web app answers on: localhost, the host of
 * RINGKEEPER_WEB_ORIGIN, and RINGKEEPER_WEB_ALLOWED_HOSTS. Anything else is
 * refused, so a website that points its own name at 127.0.0.1 (DNS rebinding)
 * can't use your browser to reach the app.
 */

const LOCAL = new Set(["localhost", "127.0.0.1", "[::1]"]);

function hostname(value: string): string | null {
  try {
    return new URL(`http://${value}`).hostname.toLowerCase();
  } catch {
    return null;
  }
}

function allowedNames(): Set<string> {
  const names = new Set(LOCAL);
  const origin = process.env.RINGKEEPER_WEB_ORIGIN;
  if (origin) {
    try {
      names.add(new URL(origin).hostname.toLowerCase());
    } catch {
      /* a malformed origin adds nothing */
    }
  }
  for (const name of (process.env.RINGKEEPER_WEB_ALLOWED_HOSTS ?? "").split(",")) {
    if (name.trim()) names.add(name.trim().toLowerCase());
  }
  return names;
}

export function hostAllowed(headers: Headers): boolean {
  const names = allowedNames();
  const ok = (value: string) => {
    const name = hostname(value);
    return name !== null && names.has(name);
  };
  const forwarded = headers.get("x-forwarded-host");
  return ok(headers.get("host") ?? "") && (forwarded === null || ok(forwarded));
}

export const HOST_REFUSED =
  "This address isn't one Ringkeeper answers on. Set RINGKEEPER_WEB_ORIGIN (or RINGKEEPER_WEB_ALLOWED_HOSTS) in .env to the address you use.";
