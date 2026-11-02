"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { fillRequest } from "@/components/composer";
import { startTour } from "@/components/tour";
import { useApi } from "@/lib/client/api";
import { DESKTOP, connectionHref, projectHref } from "@/lib/client/routes";
import { place, useScope } from "@/lib/client/scope";

interface Command {
  label: string;
  group: string;
  hint?: string;
  run: () => void;
}

const OPEN = "rk-palette";

/** Open the palette from a button (Ctrl+K does it from anywhere). */
export function openPalette(): void {
  window.dispatchEvent(new Event(OPEN));
}

/** How well `query` matches `text`: its letters in order, closer together and at
 * word starts scoring higher; null when they aren't all there. */
export function fuzzy(query: string, text: string): number | null {
  const q = query.toLowerCase().replace(/\s+/g, "");
  if (!q) return 0;
  const t = text.toLowerCase();
  let score = 0;
  let at = -1;
  for (const ch of q) {
    const next = t.indexOf(ch, at + 1);
    if (next < 0) return null;
    score += next === at + 1 ? 3 : 0; // runs of letters
    score += next === 0 || /[\s›/_-]/.test(t[next - 1] ?? "") ? 2 : 0; // word starts
    score -= Math.min(next - at - 1, 5) * 0.1; // gaps
    at = next;
  }
  return t.includes(query.toLowerCase().trim()) ? score + 10 : score;
}

const SETTINGS: [string, string][] = [
  ["claude", "Claude"],
  ["models", "Models and effort"],
  ...(DESKTOP ? ([["google", "Google app"]] as [string, string][]) : []),
  ["notifications", "Notifications (Telegram, Slack, Discord)"],
  ["advanced", "Advanced"],
  ["appearance", "Appearance"],
  ["about", "About and updates"],
];

const PAGES: [string, string][] = [
  ["/", "Home"],
  ["/approvals", "Approvals"],
  ["/tasks", "Tasks"],
  ["/projects", "Clients & projects"],
  ["/connections", "Connections"],
  ["/agents", "Agents"],
  ["/routines", "Routines"],
  ["/activity", "Activity"],
  ["/settings", "Settings"],
  ["/help", "Help"],
];

/** Ctrl+K (or Cmd+K): switch client or project, start something, open any page or
 * setting. Type to filter; arrows and Enter to pick; Escape closes. */
export function CommandPalette() {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((now) => !now);
      }
    };
    const show = () => setOpen(true);
    window.addEventListener("keydown", key);
    window.addEventListener(OPEN, show);
    return () => {
      window.removeEventListener("keydown", key);
      window.removeEventListener(OPEN, show);
    };
  }, []);
  return open ? <Palette onClose={() => setOpen(false)} /> : null;
}

function Palette({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const scope = useScope();
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const connections = useApi<{ name: string; owner: string; shared: boolean; type: string }[]>("connections");
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement | null>(null);
  const list = useRef<HTMLUListElement | null>(null);
  useEffect(() => input.current?.focus(), []);

  const commands = useMemo(() => {
    const go = (href: string) => () => router.push(href);
    const all: Command[] = [
      {
        label: "New request",
        group: "Do",
        hint: "Ask for something in plain words",
        run: () => {
          router.push("/");
          fillRequest("");
        },
      },
      { label: "Open pending approvals", group: "Do", run: go("/approvals") },
      { label: "Add a connection", group: "Do", hint: "A client's service: a database, Airtable, GitHub…", run: go("/connections/new") },
      { label: "Add a shared tool", group: "Do", hint: "Yours, for any client: a browser, files on this computer", run: go("/connections/new?kind=shared") },
      { label: "Add a client or project", group: "Do", run: go("/projects/new") },
      { label: "New agent", group: "Do", run: go("/agents?new=1") },
      { label: "New routine", group: "Do", run: go("/routines?new=1") },
      { label: "Show the tour again", group: "Do", run: () => { router.push("/"); startTour(); } },
      ...PAGES.map(([href, label]): Command => ({ label, group: "Go to", run: go(href) })),
      ...SETTINGS.map(([tab, label]): Command => ({ label: `Settings › ${label}`, group: "Settings", run: go(`/settings#${tab}`) })),
      { label: "All clients", group: "Switch to", hint: "Show everything", run: () => scope.set({ client: "", project: "" }) },
      ...scope.clients.map((c): Command => ({ label: c.name, group: "Switch to", hint: "client", run: () => scope.set({ client: c.slug, project: "" }) })),
      ...(projects.data ?? []).map((p): Command => ({
        label: place(p.owner, p.name),
        group: "Switch to",
        hint: "project",
        run: () => scope.set({ client: p.owner, project: p.name }),
      })),
      ...(projects.data ?? []).map((p): Command => ({ label: `${place(p.owner, p.name)} page`, group: "Open", run: go(projectHref(p.name)) })),
      ...(connections.data ?? []).map((c): Command => ({
        label: c.shared ? c.name : `${place(c.owner, null)} › ${c.name}`,
        group: "Open",
        hint: c.shared ? "shared tool" : "connection",
        run: go(connectionHref(c)),
      })),
    ];
    return all;
  }, [router, scope, projects.data, connections.data]);

  const shown = useMemo(() => {
    if (!query.trim()) return commands.filter((c) => c.group !== "Open").slice(0, 40);
    return commands
      .map((command) => ({ command, score: fuzzy(query, `${command.label} ${command.hint ?? ""}`) }))
      .filter((m): m is { command: Command; score: number } => m.score !== null)
      .sort((a, b) => b.score - a.score)
      .slice(0, 40)
      .map((m) => m.command);
  }, [commands, query]);

  useEffect(() => setIndex(0), [query]);
  useEffect(() => {
    list.current?.querySelector(`[data-index="${index}"]`)?.scrollIntoView({ block: "nearest" });
  }, [index]);

  function pick(command: Command | undefined) {
    if (!command) return;
    onClose();
    command.run();
  }

  return (
    <div className="fixed inset-0 z-40 bg-black/30 p-4 pt-[12vh]" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="mx-auto w-full max-w-xl overflow-hidden rounded-2xl border border-line bg-surface shadow-xl"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          ref={input}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Type a page, a client, a project or an action…"
          role="combobox"
          aria-expanded="true"
          aria-controls="palette-list"
          aria-activedescendant={shown.length ? `palette-${index}` : undefined}
          className="min-h-12 w-full border-b border-line bg-transparent px-4 text-base outline-none"
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            else if (event.key === "ArrowDown") {
              event.preventDefault();
              setIndex((n) => Math.min(n + 1, shown.length - 1));
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setIndex((n) => Math.max(n - 1, 0));
            } else if (event.key === "Enter") {
              event.preventDefault();
              pick(shown[index]);
            }
          }}
        />
        <ul id="palette-list" ref={list} role="listbox" className="max-h-[50vh] overflow-y-auto p-1">
          {shown.map((command, n) => (
            <li
              key={`${command.group}:${command.label}`}
              id={`palette-${n}`}
              data-index={n}
              role="option"
              aria-selected={n === index}
              onMouseEnter={() => setIndex(n)}
              onClick={() => pick(command)}
              className={`flex cursor-pointer items-baseline gap-2 rounded-xl px-3 py-2 text-sm ${n === index ? "bg-accent-soft" : ""}`}
            >
              <span className="w-20 shrink-0 text-xs text-muted">{command.group}</span>
              <span className="min-w-0 flex-1 truncate">{command.label}</span>
              {command.hint ? <span className="hidden truncate text-xs text-muted sm:inline">{command.hint}</span> : null}
            </li>
          ))}
          {!shown.length ? <li className="px-3 py-4 text-sm text-muted">Nothing matches “{query}”.</li> : null}
        </ul>
        <p className="border-t border-line px-4 py-2 text-xs text-muted">↑ ↓ to choose · Enter to open · Esc to close · Ctrl+K any time</p>
      </div>
    </div>
  );
}
