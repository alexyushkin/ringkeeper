/**
 * Links to a project, task or thread. The server app has a page per item
 * (/tasks/<id>); the desktop app's files are exported ahead of time, so it
 * uses the same pages by query string (/task?id=<id>). Decided at build time.
 */
/** The desktop app (static files in the app window), not the server install. */
export const DESKTOP = process.env.NEXT_PUBLIC_RINGKEEPER_TARGET === "desktop";

export function projectHref(name: string): string {
  return DESKTOP ? `/project?name=${encodeURIComponent(name)}` : `/projects/${name}`;
}

export function taskHref(id: string): string {
  return DESKTOP ? `/task?id=${encodeURIComponent(id)}` : `/tasks/${id}`;
}

export function threadHref(id: string): string {
  return DESKTOP ? `/thread?id=${encodeURIComponent(id)}` : `/threads/${id}`;
}
