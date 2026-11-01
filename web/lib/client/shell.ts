"use client";

import { DESKTOP } from "@/lib/client/routes";

/**
 * The desktop app's shell (Tauri), when this page is in its window. The shell
 * lets these pages open the folder picker and set the window's theme, nothing
 * else (see allow_pages in shell/src/main.rs). In a browser it isn't there.
 */

interface Internals {
  invoke: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
}

function internals(): Internals | null {
  if (!DESKTOP || typeof window === "undefined") return null;
  const found = (window as { __TAURI_INTERNALS__?: Partial<Internals> }).__TAURI_INTERNALS__;
  return typeof found?.invoke === "function" ? (found as Internals) : null;
}

export function canPickFolder(): boolean {
  return internals() !== null;
}

/** The native folder picker. The chosen folder, or null if cancelled. */
export async function pickFolder(title: string, start?: string): Promise<string | null> {
  const shell = internals();
  if (!shell) return null;
  const picked = await shell.invoke("plugin:dialog|open", {
    options: { title, directory: true, defaultPath: start || undefined },
  });
  return typeof picked === "string" ? picked : null;
}

/** The window's title bar: light, dark, or null for the system's setting. */
export async function setWindowTheme(theme: "light" | "dark" | null): Promise<void> {
  const shell = internals();
  if (!shell) return;
  try {
    await shell.invoke("plugin:window|set_theme", { label: "main", value: theme });
  } catch {
    // an older shell without the permission: the title bar keeps the system's
  }
}
