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

/** A client's page (their connection library): by query string in both apps. */
export function clientHref(name: string): string {
  return `/client?name=${encodeURIComponent(name)}`;
}

/** One connection: on its client's page, or (a shared tool) under Connections. The
 * hash opens the Connections tab, then that connection (LibraryCard). */
export function connectionHref(connection: { name: string; owner: string; shared: boolean }): string {
  const hash = `#connections/${encodeURIComponent(connection.name)}`;
  return connection.shared ? `/connections${hash}` : `${clientHref(connection.owner)}${hash}`;
}
