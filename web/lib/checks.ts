// A list of checkboxes (projects, clients, connections, tools), one behaviour everywhere:
// Select all and Clear, "3 of 4 selected", shift-click for a range, and with more than
// MANY rows a filter box and a group per client, each with its own Select all.

export const MANY = 8;

export interface Row {
  key: string;
  text: string; // what the filter box searches
  group?: string; // a client's name: rows are grouped by it when there are many
  disabled?: boolean; // can't be changed here: Select all and Clear leave it as it is
}

/** Every word typed is somewhere in the row's text or its group. */
export function matches(row: Row, filter: string): boolean {
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const haystack = `${row.text} ${row.group ?? ""}`.toLowerCase();
  return words.every((w) => haystack.includes(w));
}

export function shown<T extends Row>(rows: T[], filter: string): T[] {
  return filter.trim() ? rows.filter((r) => matches(r, filter)) : rows;
}

/** Adds every row that can change; the rest of the selection is kept. */
export function selectAll(selected: string[], rows: Row[]): string[] {
  const next = [...selected];
  for (const row of rows) if (!row.disabled && !next.includes(row.key)) next.push(row.key);
  return next;
}

/** Takes out every row that can change; one that can't keeps its tick. */
export function clearAll(selected: string[], rows: Row[]): string[] {
  const off = new Set(rows.filter((r) => !r.disabled).map((r) => r.key));
  return selected.filter((key) => !off.has(key));
}

/**
 * A click on one row. With shift and an earlier click in the same list, every row
 * between the two (as shown) takes the clicked row's new state.
 */
export function clicked(selected: string[], rows: Row[], key: string, on: boolean, anchor: string | null, shift: boolean): string[] {
  const at = rows.findIndex((r) => r.key === key);
  const from = shift && anchor !== null ? rows.findIndex((r) => r.key === anchor) : -1;
  const span = from >= 0 && at >= 0 ? rows.slice(Math.min(from, at), Math.max(from, at) + 1) : rows.filter((r) => r.key === key);
  return on ? selectAll(selected, span) : clearAll(selected, span); // a row that can't change is skipped
}

/** "3 of 4 selected", counting only these rows. */
export function countText(selected: string[], rows: Row[]): string {
  const on = rows.filter((r) => selected.includes(r.key)).length;
  return `${on} of ${rows.length} selected`;
}

export function allOn(selected: string[], rows: Row[]): boolean {
  const open = rows.filter((r) => !r.disabled);
  return open.length > 0 && open.every((r) => selected.includes(r.key));
}

export function noneOn(selected: string[], rows: Row[]): boolean {
  return rows.filter((r) => !r.disabled).every((r) => !selected.includes(r.key));
}

/** Rows by group, in the order groups first appear; rows without one come last. */
export function grouped<T extends Row>(rows: T[]): [string, T[]][] {
  const groups = new Map<string, T[]>();
  for (const row of rows) {
    const name = row.group ?? "";
    groups.set(name, [...(groups.get(name) ?? []), row]);
  }
  const named = [...groups.entries()].filter(([name]) => name);
  const rest = groups.get("");
  return rest ? [...named, ["", rest]] : named;
}
