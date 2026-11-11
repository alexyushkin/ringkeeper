"use client";

import { useEffect, useState, type KeyboardEvent } from "react";

import { api } from "@/lib/client/api";
import { DEFAULT_SEND_MODE, isMac, sendHint, sendKey, sendMode, type SendMode } from "@/lib/send-keys";

// Read once per page load and shared by every box (a page of approvals has many).
let known: SendMode | null = null;
let asking: Promise<void> | null = null;
const listeners = new Set<(mode: SendMode) => void>();

function tell(mode: SendMode) {
  known = mode;
  listeners.forEach((listener) => listener(mode));
}

function load() {
  asking ??= api<{ values: { send_key?: unknown } }>("settings").then(
    (settings) => tell(sendMode(settings.values.send_key)),
    () => {
      asking = null; // try again next time a box opens
    },
  );
}

/** Settings calls this after saving, so open boxes change at once. */
export function setSendMode(mode: SendMode): void {
  tell(mode);
}

function platform(): string {
  const nav = navigator as Navigator & { userAgentData?: { platform?: string } };
  return nav.userAgentData?.platform || nav.platform || nav.userAgent;
}

/** The saved choice, and whether this is a Mac (for Cmd+Enter). */
export function useSendMode(): { mode: SendMode; mac: boolean } {
  // The default until the page is in the browser: the server can't know either.
  const [mode, setMode] = useState<SendMode>(known ?? DEFAULT_SEND_MODE);
  const [mac, setMac] = useState(false);
  useEffect(() => {
    setMac(isMac(platform()));
    listeners.add(setMode);
    if (known) setMode(known);
    else load();
    return () => {
      listeners.delete(setMode);
    };
  }, []);
  return { mode, mac };
}

export interface SendKeys {
  onKeyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void;
  hint: string;
}

/** What every text box does with its keys: send (Settings > General > Send with), a
 * new line, or Esc to leave the box. Nothing is sent while the @ list is open. */
export function useSendKeys(onSend: () => void, options: { mentionOpen?: boolean; verb?: string } = {}): SendKeys {
  const { mode, mac } = useSendMode();
  return {
    hint: sendHint(mode, mac, options.verb),
    onKeyDown: (event) => {
      const action = sendKey(
        {
          key: event.key,
          ctrlKey: event.ctrlKey,
          metaKey: event.metaKey,
          shiftKey: event.shiftKey,
          altKey: event.altKey,
          isComposing: event.nativeEvent.isComposing,
          keyCode: event.keyCode,
        },
        { mode, mac, mentionOpen: options.mentionOpen },
      );
      if (action === "send") {
        event.preventDefault();
        onSend();
      } else if (action === "blur") {
        event.currentTarget.blur();
      }
    },
  };
}
