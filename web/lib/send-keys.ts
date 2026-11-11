// Which key sends from a text box, and what each key press does there. Every box
// uses this through useSendKeys, so they all behave the same.

/** Settings > General > Send with. Kept on the server, as `send_key`. */
export type SendMode = "enter" | "ctrl-enter";

export const DEFAULT_SEND_MODE: SendMode = "ctrl-enter";

/** send: send now. newline: let the box add a line. blur: leave the box. none: nothing of ours. */
export type SendAction = "send" | "newline" | "blur" | "none";

export interface KeyPress {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
  isComposing?: boolean;
  keyCode?: number;
}

export interface SendContext {
  mode: SendMode;
  mac: boolean;
  mentionOpen?: boolean; // the @ list is showing: its keys come first
}

/** A saved value as a mode; anything else is the default. */
export function sendMode(value: unknown): SendMode {
  return value === "enter" || value === "ctrl-enter" ? value : DEFAULT_SEND_MODE;
}

/** A Mac, iPhone or iPad, from navigator's platform or user agent. */
export function isMac(platform: string | undefined | null): boolean {
  return /mac|iphone|ipad|ipod/i.test(platform ?? "");
}

export function sendKey(press: KeyPress, context: SendContext): SendAction {
  // Typing with an input method (Japanese, Chinese...): Enter picks the word.
  if (press.isComposing || press.keyCode === 229) return "none";
  // The mention list picks with Enter and closes with Esc, as before.
  if (context.mentionOpen) return "none";
  if (press.key === "Escape") return "blur";
  if (press.key !== "Enter" || press.altKey) return "none";
  // Ctrl+Enter always sends, and Cmd+Enter on a Mac (the Windows key isn't one).
  if (press.ctrlKey || (context.mac && press.metaKey)) return "send";
  if (press.metaKey) return "none";
  if (press.shiftKey) return "newline";
  return context.mode === "enter" ? "send" : "newline";
}

/** The key that sends with a modifier, as this computer names it. */
export function modifierEnter(mac: boolean): string {
  return mac ? "Cmd+Enter" : "Ctrl+Enter";
}

/** The line under a box: "Ctrl+Enter to send", or "Enter to send · Shift+Enter for a new line". */
export function sendHint(mode: SendMode, mac: boolean, verb = "send"): string {
  return mode === "enter" ? `Enter to ${verb} · Shift+Enter for a new line` : `${modifierEnter(mac)} to ${verb}`;
}

/** Settings > General > Send with: the choices, the default first. */
export function sendChoices(mac: boolean): [SendMode, string][] {
  return [
    ["ctrl-enter", `${modifierEnter(mac)} (Enter for a new line)`],
    ["enter", "Enter (Shift+Enter for a new line)"],
  ];
}
