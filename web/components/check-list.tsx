"use client";

import { useId, useRef, useState, type ChangeEvent, type MouseEvent, type ReactNode } from "react";

import { allOn, clearAll, clicked, countText, grouped, MANY, noneOn, selectAll, shown, type Row } from "@/lib/checks";

/**
 * Every list of checkboxes (projects, clients, connections, tools) is this one: Select
 * all and Clear above it with "3 of 4 selected"; with more than 8 rows, a filter box
 * and a group per client, each with its own Select all. Space toggles the focused row,
 * and shift-click ticks or clears every row between it and the last one clicked.
 */

export interface CheckItem extends Row {
  label: ReactNode;
  title?: string; // shown on hover, e.g. why a row can't be ticked
  detail?: ReactNode; // under the row, e.g. what its link is narrowed to
}

/** "Select all", "Clear" and the count. */
export function BulkControls({ count, onAll, onClear, allDisabled, clearDisabled, small = false }: { count: string; onAll?: () => void; onClear: () => void; allDisabled?: boolean; clearDisabled?: boolean; small?: boolean }) {
  const button = "text-accent disabled:cursor-default disabled:text-muted disabled:opacity-60";
  return (
    <div className={`flex flex-wrap items-center gap-x-3 gap-y-1 ${small ? "text-xs" : "text-sm"}`} data-bulk>
      {onAll ? (
        <button type="button" className={button} disabled={allDisabled} onClick={onAll}>
          Select all
        </button>
      ) : null}
      <button type="button" className={button} disabled={clearDisabled} onClick={onClear}>
        Clear
      </button>
      <span className="text-muted" aria-live="polite">
        {count}
      </span>
    </div>
  );
}

/** Shift-click over a list whose rows are drawn elsewhere (the task and conversation lists). */
export function useRange() {
  const anchor = useRef<string | null>(null);
  return {
    click(selected: string[], rows: Row[], key: string, on: boolean, shift: boolean): string[] {
      const next = clicked(selected, rows, key, on, anchor.current, shift);
      anchor.current = key;
      return next;
    },
  };
}

export function shiftOf(event: ChangeEvent<HTMLInputElement> | MouseEvent<HTMLElement>): boolean {
  const native = event.nativeEvent as { shiftKey?: boolean };
  return Boolean(native.shiftKey);
}

/** Ticked rows that each carry something (a link's narrowing): kept for rows still ticked. */
export function keepPicked<T>(picked: Record<string, T>, next: string[], blank: () => T): Record<string, T> {
  return Object.fromEntries(next.map((key) => [key, picked[key] ?? blank()]));
}

/** Holding shift on a label doesn't select its text. */
function noTextSelect(event: MouseEvent<HTMLElement>) {
  if (event.shiftKey) event.preventDefault();
}

export function CheckList({
  legend,
  items: given,
  selected,
  onChange,
  hint,
  oneGroup = false,
  oneGroupTitle = "Only one client's at a time",
  groups = "many",
  className = "",
  align = "center",
  boxed = false,
}: {
  legend?: ReactNode;
  items: CheckItem[];
  selected: string[];
  onChange: (next: string[]) => void;
  hint?: ReactNode;
  oneGroup?: boolean; // only one group's rows may be ticked (a client's connection): no Select all across them
  oneGroupTitle?: string;
  groups?: "many" | "always"; // group by client with more than 8 rows, or whenever rows have groups
  className?: string;
  align?: "center" | "start"; // "start": rows of more than one line
  boxed?: boolean; // each row in its own box
}) {
  const [filter, setFilter] = useState("");
  const range = useRange();
  const filterId = useId();
  // One group only: once a row is ticked, the other groups' rows wait until it's cleared.
  const ticked = oneGroup ? given.find((i) => selected.includes(i.key)) : undefined;
  const items = ticked ? given.map((i) => ((i.group ?? "") === (ticked.group ?? "") || i.disabled ? i : { ...i, disabled: true, title: i.title ?? oneGroupTitle })) : given;
  const long = items.length > MANY;
  const rows = long ? shown(items, filter) : items;
  const byGroup = (long || groups === "always") && items.some((i) => i.group) ? grouped(rows) : null;
  const several = new Set(given.map((i) => i.group ?? "")).size > 1;
  const set = (next: string[]) => onChange(next);

  const row = (item: CheckItem) => {
    const on = selected.includes(item.key);
    return (
      <div key={item.key} className={boxed ? "space-y-2 rounded-xl border border-line p-3" : "space-y-1"}>
        <label className={`flex ${align === "start" ? "items-start" : "items-center"} gap-2 text-sm ${item.disabled ? "opacity-50" : ""}`} title={item.title} onMouseDown={noTextSelect}>
          <input
            type="checkbox"
            disabled={item.disabled}
            checked={on}
            onChange={(event) => set(range.click(selected, rows, item.key, event.target.checked, shiftOf(event)))}
            className={`size-4 shrink-0 accent-[var(--color-accent)] ${align === "start" ? "mt-0.5" : ""}`}
          />
          {item.label}
        </label>
        {item.detail ? <div className="ml-6">{item.detail}</div> : null}
      </div>
    );
  };

  return (
    <fieldset className={`space-y-2 ${className}`}>
      {legend ? <legend className="text-sm font-medium">{legend}</legend> : null}
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
      {long ? (
        <div>
          <label htmlFor={filterId} className="sr-only">
            Filter
          </label>
          <input
            id={filterId}
            type="search"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter"
            className="w-full rounded-lg border border-line bg-surface px-3 py-1.5 text-sm"
          />
        </div>
      ) : null}
      {items.length > 1 ? (
        <BulkControls
          count={filter.trim() ? `${countText(selected, rows)} (of ${items.length})` : countText(selected, items)}
          onAll={oneGroup && several ? undefined : () => set(selectAll(selected, rows))}
          onClear={() => set(clearAll(selected, rows))}
          allDisabled={allOn(selected, rows)}
          clearDisabled={noneOn(selected, rows)}
        />
      ) : null}
      {byGroup ? (
        <div className={`space-y-3 ${long ? "max-h-80 overflow-y-auto rounded-xl border border-line p-2" : ""}`}>
          {byGroup.map(([name, list]) => (
            <div key={name || "none"} className="space-y-1" role="group" aria-label={name || "Others"}>
              <div className="flex flex-wrap items-center gap-x-3">
                <p className="text-xs font-semibold text-muted">{name || "Others"}</p>
                {list.length > 1 ? (
                  <BulkControls
                    small
                    count={countText(selected, list)}
                    onAll={() => set(selectAll(selected, list))}
                    onClear={() => set(clearAll(selected, list))}
                    allDisabled={allOn(selected, list)}
                    clearDisabled={noneOn(selected, list)}
                  />
                ) : null}
              </div>
              {list.map(row)}
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-1">{rows.map(row)}</div>
      )}
      {long && filter.trim() && !rows.length ? <p className="text-sm text-muted">Nothing matches.</p> : null}
    </fieldset>
  );
}
