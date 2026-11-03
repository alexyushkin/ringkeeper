"use client";

import { useCallback } from "react";

import { api, useApi } from "@/lib/client/api";

/** What's pinned and what was used lately, in the switcher and Ctrl+K: "client:<slug>" or
 * "project:<name>". Kept on the server: the desktop app's address changes each launch. */
export interface Prefs {
  pins: string[];
  recents: string[];
}

const RECENTS = 8;

export function usePrefs() {
  const prefs = useApi<Prefs>("ui-prefs");
  const { reload } = prefs;
  const pins = prefs.data?.pins ?? [];
  const recents = prefs.data?.recents ?? [];
  const key = `${pins.join()}|${recents.join()}`;

  const save = useCallback(
    (next: Prefs) => void api("ui-prefs", { method: "PUT", json: next }).then(reload, () => undefined),
    [reload],
  );
  const togglePin = useCallback(
    (item: string) => save({ recents, pins: pins.includes(item) ? pins.filter((p) => p !== item) : [item, ...pins] }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, save],
  );
  const touch = useCallback(
    (item: string) => save({ pins, recents: [item, ...recents.filter((r) => r !== item)].slice(0, RECENTS) }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [key, save],
  );
  return { pins, recents, togglePin, touch };
}
