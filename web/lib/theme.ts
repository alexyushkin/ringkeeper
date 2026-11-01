/**
 * Light, dark, or the system's setting (the default). The choice is a cookie,
 * not localStorage: the desktop app's backend gets a new port each launch, and
 * cookies don't depend on the port. The page reads it before it draws
 * (app/layout.tsx), as `data-theme` on <html>, which globals.css follows.
 */

export type Theme = "system" | "light" | "dark";

export const THEME_COOKIE = "rk-theme";

/** For the exported desktop pages, which have no server to read the cookie. */
export const THEME_SCRIPT = `try{var t=document.cookie.match(/(?:^|; )${THEME_COOKIE}=(light|dark)(?:;|$)/);if(t)document.documentElement.dataset.theme=t[1]}catch(e){}`;

export function chosenTheme(value: string | undefined | null): "light" | "dark" | undefined {
  return value === "light" || value === "dark" ? value : undefined;
}
