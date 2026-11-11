// What an approval card shows without a click, and what the "waiting" bar says.

export interface Email {
  to: string[];
  cc: string[];
  subject: string;
  body: string;
}

type Args = Record<string, unknown>;

const isObject = (value: unknown): value is Args => typeof value === "object" && value !== null && !Array.isArray(value);

/** Addresses from a list of strings, one string, or Microsoft's [{emailAddress: {address}}]. */
function addresses(value: unknown): string[] {
  if (typeof value === "string") return value.split(/[,;]\s*/).filter(Boolean);
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (typeof item === "string") return item;
      if (isObject(item) && isObject(item.emailAddress)) {
        const { address, name } = item.emailAddress;
        return typeof address === "string" ? (typeof name === "string" && name && name !== address ? `${name} <${address}>` : address) : "";
      }
      return "";
    })
    .filter(Boolean);
}

/** HTML as plain text: line breaks kept, tags dropped. */
export function plainBody(html: string): string {
  return html
    .replace(/<(br|\/p|\/div|\/li|\/h\d)\b[^>]*>/gi, "\n")
    .replace(/<[^>]+>/g, "")
    .replace(/&nbsp;/g, " ")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&quot;/g, '"')
    .replace(/&#39;/g, "'")
    .replace(/&amp;/g, "&")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function bodyText(value: unknown, fallbackHtml: unknown): string {
  if (typeof value === "string") return value;
  if (isObject(value) && typeof value.content === "string") {
    return String(value.contentType ?? "").toLowerCase() === "html" ? plainBody(value.content) : value.content;
  }
  return typeof fallbackHtml === "string" ? plainBody(fallbackHtml) : "";
}

/** The email a held call would send or save, or null when it isn't one. Covers the mail
 * connections' own tools (to, subject, body) and a business system's operations, whose
 * message is in `body` (Outlook: `body.message` for sending). */
export function emailOf(args: Args | null | undefined): Email | null {
  if (!args) return null;
  let message: Args = args;
  if (typeof args.operation === "string" && isObject(args.body)) {
    message = isObject(args.body.message) ? args.body.message : args.body;
  }
  const to = addresses(message.to ?? message.toRecipients ?? message.recipients);
  const subject = typeof message.subject === "string" ? message.subject : "";
  if (!to.length && !subject) return null;
  return {
    to,
    cc: addresses(message.cc ?? message.ccRecipients),
    subject,
    body: bodyText(message.body ?? message.text ?? message.comment, message.html),
  };
}

export const BODY_LINES = 12;

/** A body cut after `limit` lines (null when it's short enough to show whole). */
export function collapsed(body: string, limit = BODY_LINES): { shown: string; total: number } | null {
  const lines = body.split("\n");
  return lines.length > limit ? { shown: lines.slice(0, limit).join("\n"), total: lines.length } : null;
}

const NOUN: Record<string, string> = { approve: "approval", question: "question", plan: "plan" };

/** "1 approval waiting", "2 questions waiting", or "3 things waiting for you". */
export function waitingLabel(kinds: string[]): string {
  const n = kinds.length;
  const all = new Set(kinds.map((k) => NOUN[k] ?? "thing"));
  if (all.size === 1) {
    const [noun] = [...all];
    if (noun !== "thing") return `${n} ${noun}${n === 1 ? "" : "s"} waiting`;
  }
  return `${n} ${n === 1 ? "thing" : "things"} waiting for you`;
}

/** Oldest first: the newest one is last, under what came before it. */
export function inOrder<T extends { created_at: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
}
