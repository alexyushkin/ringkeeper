"use client";

import { useEffect } from "react";

/** Registers /sw.js, which caches the app shell only (never data), so the app installs. */
export function ServiceWorker() {
  useEffect(() => {
    if ("serviceWorker" in navigator && window.isSecureContext) {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => undefined);
    }
  }, []);
  return null;
}
