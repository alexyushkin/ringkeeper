import type { ReactNode } from "react";

import { readTable, startsTable } from "@/lib/markdown-table";

/**
 * The Markdown Claude writes, as React elements: paragraphs, headings, lists,
 * quotes, code, tables, bold, italics, strikethrough and links. No HTML from the
 * text is ever used (React escapes it all), and links go only to http(s) or
 * mailto addresses, opened outside the app.
 */
export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return <div className={`space-y-2 break-words ${className}`}>{blocks(text.replace(/\r\n?/g, "\n").split("\n"))}</div>;
}

const FENCE = /^\s*(```|~~~)/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const ITEM = /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/;
const QUOTE = /^\s{0,3}>\s?(.*)$/;
const RULE = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/;
const ROW = /^\s*\|.*\|\s*$/;
const DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

function special(line: string, next: string | undefined): boolean {
  return FENCE.test(line) || HEADING.test(line) || ITEM.test(line) || QUOTE.test(line) || RULE.test(line) || (ROW.test(line) && next !== undefined && DIVIDER.test(next));
}

function blocks(lines: string[]): ReactNode[] {
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const key = out.length;
    if (!line.trim()) {
      i++;
      continue;
    }
    const fence = FENCE.exec(line);
    if (fence) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !(lines[i] ?? "").trimStart().startsWith(fence[1] ?? "```")) code.push(lines[i++] ?? "");
      i++;
      out.push(
        <pre key={key} className="max-h-96 overflow-auto rounded-xl bg-sunken p-3 text-xs leading-relaxed">
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      out.push(
        <p key={key} className={(heading[1] ?? "").length <= 2 ? "pt-1 text-base font-semibold" : "pt-1 font-semibold"}>
          {inline(heading[2] ?? "")}
        </p>,
      );
      i++;
      continue;
    }
    if (RULE.test(line)) {
      out.push(<hr key={key} className="border-line" />);
      i++;
      continue;
    }
    if (QUOTE.test(line)) {
      const quoted: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i] ?? "")) quoted.push(QUOTE.exec(lines[i++] ?? "")?.[1] ?? "");
      out.push(
        <blockquote key={key} className="space-y-2 border-l-2 border-line pl-3 text-muted">
          {blocks(quoted)}
        </blockquote>,
      );
      continue;
    }
    if (startsTable(lines, i)) {
      const { head, rows, next } = readTable(lines, i);
      i = next;
      out.push(
        <div key={key} className="overflow-x-auto">
          <table className="text-left">
            <thead>
              <tr>
                {head.map((cell, c) => (
                  <th key={c} className="border-b border-line px-2 py-1 font-semibold">
                    {inline(cell)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((row, r) => (
                <tr key={r}>
                  {head.map((_, c) => (
                    <td key={c} className="border-b border-line px-2 py-1 align-top">
                      {inline(row[c] ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
      continue;
    }
    if (ITEM.test(line)) {
      const [list, next] = items(lines, i);
      out.push(<ListView key={key} list={list} />);
      i = next;
      continue;
    }
    // A paragraph: lines up to a blank line or another kind of block, line breaks kept.
    const text: string[] = [];
    while (i < lines.length && (lines[i] ?? "").trim() && !(text.length && special(lines[i] ?? "", lines[i + 1]))) text.push(lines[i++] ?? "");
    out.push(
      <p key={key}>
        {text.map((t, n) => (
          <span key={n}>
            {n ? <br /> : null}
            {inline(t.trim())}
          </span>
        ))}
      </p>,
    );
  }
  return out;
}


interface List {
  ordered: boolean;
  start: number;
  items: string[][]; // each item's lines, its own indent removed: nested lists come out as lists again
}

/** A list starting at `from`: its items, with what's indented under each, up to where it ends. */
function items(lines: string[], from: number): [List, number] {
  const first = ITEM.exec(lines[from] ?? "");
  const indent = first?.[1]?.length ?? 0;
  const ordered = /\d/.test(first?.[2] ?? "");
  const list: List = { ordered, start: ordered ? parseInt(first?.[2] ?? "1", 10) : 1, items: [] };
  let i = from;
  while (i < lines.length) {
    const line = lines[i] ?? "";
    const item = ITEM.exec(line);
    if (item && (item[1]?.length ?? 0) <= indent) {
      if (/\d/.test(item[2] ?? "") !== ordered) break;
      list.items.push([item[3] ?? ""]);
      i++;
      continue;
    }
    const current = list.items[list.items.length - 1];
    if (!current) break;
    if (!line.trim()) {
      // A blank line ends the list unless more of it follows, indented or as another item.
      const after = lines[i + 1] ?? "";
      const more = ITEM.exec(after);
      if (after.trim() && (/^\s/.test(after) ? after.search(/\S/) > indent : more && (more[1]?.length ?? 0) <= indent && /\d/.test(more[2] ?? "") === ordered)) {
        current.push("");
        i++;
        continue;
      }
      break;
    }
    if (line.search(/\S/) > indent) {
      current.push(line.slice(Math.min(line.search(/\S/), indent + 4)));
      i++;
      continue;
    }
    if (special(line, lines[i + 1])) break;
    current.push(line.trim()); // a lazy continuation of the item's text
    i++;
  }
  return [list, i];
}

function ListView({ list }: { list: List }) {
  const children = list.items.map((lines, n) => (
    <li key={n} className="space-y-1 [&>div]:space-y-1">
      {lines.length === 1 ? inline(lines[0] ?? "") : <div>{blocks(lines)}</div>}
    </li>
  ));
  return list.ordered ? (
    <ol start={list.start} className="list-decimal space-y-1 pl-6">
      {children}
    </ol>
  ) : (
    <ul className="list-disc space-y-1 pl-6">{children}</ul>
  );
}

// Code first (nothing inside it is Markdown), then links, then emphasis.
const INLINE =
  /(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)|\[([^\]\n]+)\]\(\s*<?([^)\s>]+)>?\s*\)|(https?:\/\/[^\s<>()]*[^\s<>().,;:!?'"*_])|\*\*(?=\S)([\s\S]*?\S)\*\*|__(?=\S)([\s\S]*?\S)__(?![\w])|~~(?=\S)([\s\S]*?\S)~~|\*(?=[^\s*])([\s\S]*?[^\s*])\*|_(?=[^\s_])([\s\S]*?[^\s_])_(?![\w])/g;

/** The words alone, for a preview cut to a few lines. */
export function plainText(text: string): string {
  const flat = (part: string): string =>
    part.replace(INLINE, (whole: string, _ticks, code?: string, label?: string, _url?: string, bare?: string, bold?: string, bold2?: string, strike?: string, em?: string, em2?: string, at = 0) =>
      (bold2 !== undefined || em2 !== undefined) && insideWord(part, at) ? whole : (code ?? bare ?? flat(label ?? bold ?? bold2 ?? strike ?? em ?? em2 ?? "")),
    );
  const blocks = text
    .replace(/^\s*```.*$/gm, "")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s{0,3}>\s?/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "• ");
  return flat(blocks).replace(/\s+/g, " ").trim();
}

/** `_` and `__` only open emphasis at the start of a word: snake_case stays as it is. */
function insideWord(text: string, at: number): boolean {
  return at > 0 && /\w/.test(text.charAt(at - 1));
}

function safeHref(url: string): string | null {
  return /^(https?:\/\/|mailto:)/i.test(url) ? url : null;
}

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(INLINE)) {
    const at = match.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    const key = out.length;
    const [whole, , code, label, url, bare, bold, bold2, strike, em, em2] = match;
    if ((bold2 !== undefined || em2 !== undefined) && insideWord(text, at)) {
      out.push(whole);
    } else if (code !== undefined) {
      out.push(
        <code key={key} className="rounded bg-sunken px-1 font-mono text-[0.9em]">
          {code.replace(/^ (.*) $/, "$1")}
        </code>,
      );
    } else if (label !== undefined) {
      const href = safeHref(url ?? "");
      out.push(
        href ? (
          <a key={key} href={href} target="_blank" rel="noreferrer" className="text-accent underline">
            {inline(label)}
          </a>
        ) : (
          whole
        ),
      );
    } else if (bare !== undefined) {
      out.push(
        <a key={key} href={bare} target="_blank" rel="noreferrer" className="text-accent underline">
          {bare}
        </a>,
      );
    } else if (bold !== undefined || bold2 !== undefined) {
      out.push(<strong key={key}>{inline(bold ?? bold2 ?? "")}</strong>);
    } else if (strike !== undefined) {
      out.push(<del key={key}>{inline(strike)}</del>);
    } else {
      out.push(<em key={key}>{inline(em ?? em2 ?? "")}</em>);
    }
    last = at + whole.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}
