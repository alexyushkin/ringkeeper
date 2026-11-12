/**
 * Markdown tables, as lines: where one starts and ends, and text without them. Pure, so
 * `node --test` checks it (lib/markdown-table.test.mjs).
 *
 * A long table can arrive with a stray blank line between its rows: Claude writes the rows
 * of a paged result as each page comes in. A blank line followed by more rows of the same
 * width (and not by a new table's divider) is still the same table.
 */

const ROW = /^\s*\|.*\|\s*$/;
const DIVIDER = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/;

export function isRow(line: string | undefined): boolean {
  return line !== undefined && ROW.test(line);
}

export function isDivider(line: string | undefined): boolean {
  return line !== undefined && DIVIDER.test(line);
}

export function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Whether a table starts at `at`: a row, then its divider. */
export function startsTable(lines: string[], at: number): boolean {
  return isRow(lines[at]) && isDivider(lines[at + 1]);
}

/** The table starting at `at` (see startsTable): its header, its rows, and the line after it. */
export function readTable(lines: string[], at: number): { head: string[]; rows: string[][]; next: number } {
  const head = cells(lines[at] ?? "");
  const rows: string[][] = [];
  let i = at + 2;
  for (;;) {
    if (isRow(lines[i]) && !startsTable(lines, i)) {
      rows.push(cells(lines[i] ?? ""));
      i++;
      continue;
    }
    // A stray blank line inside the table: more rows of the same width follow.
    let after = i;
    while (after < lines.length && !(lines[after] ?? "").trim()) after++;
    const same = isRow(lines[after]) && !startsTable(lines, after) && cells(lines[after] ?? "").length === head.length;
    if (after > i && same) {
      i = after;
      continue;
    }
    return { head, rows, next: i };
  }
}

/** The text without its tables and code blocks: for a one-line preview. */
/** A report's main table: the one with the most rows (the first of those), or null when
 * there's none. A self-test report's summary comes before its table of scenarios. */
export function mainTable(text: string): { head: string[]; rows: string[][] } | null {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  let found: { head: string[]; rows: string[][] } | null = null;
  for (let i = 0; i < lines.length; i++) {
    if (startsTable(lines, i)) {
      const { head, rows, next } = readTable(lines, i);
      if (!found || rows.length > found.rows.length) found = { head, rows };
      i = next - 1;
    }
  }
  return found;
}

export function withoutTables(text: string): string {
  const lines = text.replace(/\r\n?/g, "\n").split("\n");
  const out: string[] = [];
  let fenced = false;
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i] ?? "";
    if (/^\s*(```|~~~)/.test(line)) {
      fenced = !fenced;
      continue;
    }
    if (fenced) continue;
    if (startsTable(lines, i)) {
      i = readTable(lines, i).next - 1;
      continue;
    }
    if (isRow(line) || isDivider(line)) continue;
    out.push(line);
  }
  return out.join("\n");
}

/** The first sentence or two of plain text, at most `limit` characters. */
export function firstSentences(text: string, count = 2, limit = 240): string {
  const flat = text.replace(/\s+/g, " ").trim();
  const ends = [...flat.matchAll(/[.!?](?=\s|$)/g)].map((m) => (m.index ?? 0) + 1);
  const cut = ends.length >= count ? flat.slice(0, ends[count - 1]) : flat;
  return cut.length <= limit ? cut : `${cut.slice(0, limit - 1).trimEnd()}…`;
}

/** A cell as plain text: no bold, italics, code marks or link syntax (a link keeps its text). */
export function plainCell(cell: string): string {
  return cell
    .replace(/\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/(\*\*|__|~~|`)/g, "")
    .replace(/(^|\s)[*_]([^*_]+)[*_](?=\s|$)/g, "$1$2")
    .replace(/\\\|/g, "|")
    .trim();
}

/** The table as CSV (RFC 4180: quoted where needed, CRLF), for a spreadsheet. */
export function toCsv(head: string[], rows: string[][]): string {
  const field = (cell: string) => {
    const text = plainCell(cell);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  return [head, ...rows].map((row) => head.map((_, c) => field(row[c] ?? "")).join(",")).join("\r\n") + "\r\n";
}

/** The table as tab-separated lines: what pasting into a spreadsheet expects. */
export function toTsv(head: string[], rows: string[][]): string {
  return [head, ...rows].map((row) => head.map((_, c) => plainCell(row[c] ?? "").replace(/[\t\n]/g, " ")).join("\t")).join("\n");
}
