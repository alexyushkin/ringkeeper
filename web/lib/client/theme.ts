"use client";

import { useEffect } from "react";

import { setWindowTheme } from "@/lib/client/shell";
import { THEME_COOKIE, chosenTheme, type Theme } from "@/lib/theme";

const YEAR = 365 * 24 * 60 * 60;

export function readTheme(): Theme {
  const match = document.cookie.match(new RegExp(`(?:^|; )${THEME_COOKIE}=([^;]*)`));
  return chosenTheme(match?.[1]) ?? "system";
}

/** Saves the choice in this browser (or the desktop window) and shows it now. */
export function applyTheme(theme: Theme) {
  const chosen = chosenTheme(theme);
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${THEME_COOKIE}=${chosen ?? ""}; Path=/; Max-Age=${chosen ? YEAR : 0}; SameSite=Lax${secure}`;
  if (chosen) document.documentElement.dataset.theme = chosen;
  else delete document.documentElement.dataset.theme;
  void setWindowTheme(chosen ?? null);
}

/** The desktop window's title bar follows the choice too (it forgets on restart). */
export function WindowTheme() {
  useEffect(() => {
    const chosen = chosenTheme(readTheme());
    if (chosen) void setWindowTheme(chosen);
  }, []);
  return null;
}
