"use client";

import Link from "@/components/link";
import { useEffect, useId, useState, type ReactNode } from "react";

import type { SendKeys } from "@/lib/client/use-send-keys";
import { namedInHash } from "@/lib/settings-sections";

/* Small building blocks. Plain language on the surface; technical detail behind "Show details". */

type Tone = "neutral" | "good" | "warn" | "bad" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-sunken text-muted border-line",
  good: "bg-good-soft text-good border-transparent",
  warn: "bg-warn-soft text-warn border-transparent",
  bad: "bg-bad-soft text-bad border-transparent",
  accent: "bg-accent-soft text-accent border-transparent",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}>
      {children}
    </span>
  );
}

export function EnvBadge({ env }: { env: string }) {
  if (env === "prod") return <Badge tone="bad">Production</Badge>;
  if (env === "staging") return <Badge tone="warn">Staging</Badge>;
  return <Badge>Test</Badge>;
}

const STATUS: Record<string, [Tone, string]> = {
  queued: ["neutral", "Waiting to start"],
  running: ["accent", "Working"],
  waiting: ["warn", "Waiting for you"],
  needs_input: ["warn", "Has a question"],
  succeeded: ["good", "Done"],
  failed: ["bad", "Failed"],
  timed_out: ["bad", "Took too long"],
  stopped: ["neutral", "Stopped"],
  budget_exceeded: ["bad", "Over budget"],
  not_done: ["bad", "Not done"],
};

export function StatusBadge({ status }: { status: string }) {
  const [tone, label] = STATUS[status] ?? ["neutral", "Other"];
  return <Badge tone={tone}>{label}</Badge>;
}

type ButtonProps = {
  children: ReactNode;
  onClick?: () => void;
  kind?: "primary" | "secondary" | "danger" | "ghost";
  type?: "button" | "submit";
  disabled?: boolean;
  busy?: boolean;
  className?: string;
  label?: string;
};

export function Button({ children, onClick, kind = "secondary", type = "button", disabled, busy, className = "", label }: ButtonProps) {
  const styles = {
    primary: "bg-accent text-accent-ink hover:opacity-90",
    secondary: "bg-surface text-ink border border-line hover:bg-sunken",
    danger: "bg-bad text-white hover:opacity-90",
    ghost: "text-muted hover:text-ink hover:bg-sunken",
  }[kind];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled || busy}
      aria-label={label}
      className={`inline-flex min-h-11 items-center justify-center gap-2 rounded-xl px-4 text-sm font-medium transition disabled:opacity-50 ${styles} ${className}`}
    >
      {busy ? <Spinner /> : null}
      {children}
    </button>
  );
}

/** A button for something that can't be undone. The first click only asks, in place;
 * the button that says what happens does it. Not window.confirm: the desktop app's
 * window doesn't show the browser's dialogs, so the action ran on the first click. */
export function ConfirmButton({
  children,
  question,
  confirm,
  onConfirm,
  kind = "danger",
  ask = false,
}: {
  children: ReactNode;
  question: ReactNode;
  confirm: string;
  onConfirm: () => Promise<unknown> | unknown;
  kind?: "danger" | "secondary";
  /** Show the question already (Ctrl+K's "Run ..."): the action still waits for its OK. */
  ask?: boolean;
}) {
  const [asking, setAsking] = useState(ask);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (ask) setAsking(true);
  }, [ask]);
  if (!asking) {
    return (
      <span className="inline-flex flex-col gap-2">
        <Button kind={kind} onClick={() => { setError(null); setAsking(true); }}>
          {children}
        </Button>
        {error ? <Alert>Couldn&apos;t do that: {error}</Alert> : null}
      </span>
    );
  }
  async function go() {
    setBusy(true);
    try {
      await onConfirm();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
      setAsking(false);
    }
  }
  return (
    <div role="alertdialog" aria-label={confirm} className="space-y-2 rounded-xl border border-bad p-3">
      <p className="text-sm">{question}</p>
      <div className="flex flex-wrap gap-2">
        <Button kind="danger" busy={busy} onClick={go}>
          {confirm}
        </Button>
        <Button kind="ghost" onClick={() => setAsking(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

export function LinkButton({ href, children, kind = "secondary" }: { href: string; children: ReactNode; kind?: "primary" | "secondary" }) {
  const styles = kind === "primary" ? "bg-accent text-accent-ink" : "bg-surface border border-line text-ink";
  return (
    <Link href={href} className={`inline-flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-medium ${styles}`}>
      {children}
    </Link>
  );
}

export function Logo({ className = "size-6" }: { className?: string }) {
  return <img src="/icon.svg" alt="" aria-hidden className={`inline-block shrink-0 ${className}`} />;
}

export function Spinner() {
  return <span aria-hidden className="inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent" />;
}

/**
 * Whether the URL names this section (`#tab/id`, as Ctrl+K opens Settings): it's scrolled
 * into view, and `run` says the URL asked to run it too (`#tab/id/run`).
 */
export function useNamedHere(id: string | undefined): { named: boolean; run: boolean } {
  const [named, setNamed] = useState({ named: false, run: false });
  useEffect(() => {
    if (!id) return;
    const timers: ReturnType<typeof setTimeout>[] = [];
    const follow = () => {
      const { section, run } = namedInHash(window.location.hash);
      const here = section === id;
      setNamed({ named: here, run: here && run });
      // Again later: the sections above may still be loading their data and push it down.
      if (here) for (const wait of [50, 500, 1200]) timers.push(setTimeout(() => document.getElementById(`section-${id}`)?.scrollIntoView({ block: "start", behavior: "smooth" }), wait));
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("hashchange", follow);
      timers.forEach(clearTimeout);
    };
  }, [id]);
  return named;
}

/** `anchor`: its id on the page, so a link (Ctrl+K) can scroll to it. */
export function Card({ children, className = "", anchor }: { children: ReactNode; className?: string; anchor?: string }) {
  useNamedHere(anchor);
  return (
    <section id={anchor ? `section-${anchor}` : undefined} className={`scroll-mt-4 rounded-2xl border border-line bg-surface p-4 ${className}`}>
      {children}
    </section>
  );
}

export function PageHeader({ title, subtitle, action, help }: { title: string; subtitle?: string; action?: ReactNode; help?: ReactNode }) {
  return (
    <header className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">
          {title}
          {help}
        </h1>
        {subtitle ? <p className="mt-1 text-sm text-muted">{subtitle}</p> : null}
      </div>
      {action}
    </header>
  );
}

export function Alert({ tone = "bad", children }: { tone?: Tone; children: ReactNode }) {
  return (
    <div role={tone === "bad" ? "alert" : "status"} className={`rounded-xl px-3 py-2 text-sm ${TONES[tone]}`}>
      {children}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="rounded-2xl border border-dashed border-line p-6 text-center text-sm text-muted">{children}</p>;
}

export function Loading() {
  return (
    <p className="flex items-center gap-2 p-4 text-sm text-muted">
      <Spinner /> Loading…
    </p>
  );
}

type FieldProps = {
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
  placeholder?: string;
  hint?: ReactNode;
  required?: boolean;
  autoComplete?: string;
  name?: string;
  inputMode?: "text" | "url" | "numeric" | "decimal";
};

export function Field({ label, value, onChange, type = "text", placeholder, hint, required, autoComplete, name, inputMode }: FieldProps) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        value={value}
        required={required}
        placeholder={placeholder}
        autoComplete={autoComplete ?? (type === "password" ? "off" : undefined)}
        inputMode={inputMode}
        spellCheck={type === "password" ? false : undefined}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-xl border border-control bg-surface px-3 text-base outline-none focus:border-accent"
      />
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

/** A box of text. With `keys` (useSendKeys), a key sends it, and the line under it says which. */
export function TextArea({ label, value, onChange, rows = 3, hint, placeholder, keys }: { label: string; value: string; onChange: (v: string) => void; rows?: number; hint?: ReactNode; placeholder?: string; keys?: SendKeys }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <textarea
        id={id}
        rows={rows}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={keys?.onKeyDown}
        className="w-full rounded-xl border border-control bg-surface px-3 py-2 text-base outline-none focus:border-accent"
      />
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
      {keys ? <p className="text-xs text-muted">{keys.hint}</p> : null}
    </div>
  );
}

export function Select({ label, value, onChange, options, hint }: { label: string; value: string; onChange: (v: string) => void; options: [string, string][]; hint?: ReactNode }) {
  const id = useId();
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        {label}
      </label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="min-h-11 w-full rounded-xl border border-control bg-surface px-3 text-base"
      >
        {options.map(([v, text]) => (
          <option key={v} value={v}>
            {text}
          </option>
        ))}
      </select>
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: ReactNode }) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <label htmlFor={id} className="text-sm font-medium">
          {label}
        </label>
        {hint ? <p className="mt-0.5 text-xs text-muted">{hint}</p> : null}
      </div>
      <input id={id} type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} className="mt-1 size-5 accent-[var(--color-accent)]" />
    </div>
  );
}

/** Technical detail stays out of the way until asked for. */
export function Details({ children, label = "Show details", defaultOpen = false }: { children: ReactNode; label?: string; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} className="text-sm text-accent" aria-expanded={open}>
        {open && label === "Show details" ? "Hide details" : label}
      </button>
      {open ? <div className="mt-2">{children}</div> : null}
    </div>
  );
}

export function Code({ children }: { children: string }) {
  return <pre className="max-h-96 overflow-auto rounded-xl bg-sunken p-3 text-xs leading-relaxed whitespace-pre-wrap break-words">{children}</pre>;
}

/** "for 52 more minutes". */
export function timeUntil(value: string): string {
  const minutes = Math.max(1, Math.round((new Date(value).getTime() - Date.now()) / 60_000));
  return `for ${minutes} more minute${minutes === 1 ? "" : "s"}`;
}

/** "today at 3:00 PM", "tomorrow at 9:00 AM", "Tue at 3:00 PM", or "Nov 20 at 3:00 PM" when it's
 * more than six days away. */
export function clock(value: string): string {
  const at = new Date(value);
  const time = at.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
  const day = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  const days = Math.round((day(at) - day(new Date())) / 86_400_000);
  if (days === 0) return `today at ${time}`;
  if (days === 1) return `tomorrow at ${time}`;
  const date = Math.abs(days) > 6 ? at.toLocaleDateString([], { month: "short", day: "numeric" }) : at.toLocaleDateString([], { weekday: "short" });
  return `${date} at ${time}`;
}

export function timeAgo(value: string | null | undefined): string {
  if (!value) return "";
  const seconds = Math.round((Date.now() - new Date(value).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return new Date(value).toLocaleDateString();
}

export function money(value: number | null | undefined): string {
  return value === null || value === undefined ? "–" : `~$${value.toFixed(value < 1 ? 3 : 2)}`;
}

/* Long pages: tabs, collapsible sections and compact rows. What's open is
 * remembered in this browser (per page), and the open tab is in the address. */

function readStored(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function writeStored(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // private window or blocked storage: it just isn't remembered
  }
}

/** A choice remembered in this browser under `key` (falls back to `initial`). */
export function useRemembered(key: string, initial: string): [string, (next: string) => void] {
  const [value, setValue] = useState(initial);
  useEffect(() => {
    const stored = readStored(key);
    if (stored !== null) setValue(stored);
  }, [key]);
  return [
    value,
    (next: string) => {
      setValue(next);
      writeStored(key, next);
    },
  ];
}

export interface Tab {
  key: string;
  label: string;
  count?: number | string;
  content: () => ReactNode;
}

/** Tabs for a long page. `id` names the page; the open tab is also in the URL (#key). */
export function Tabs({ id, tabs }: { id: string; tabs: Tab[] }) {
  const [stored, setStored] = useRemembered(`rk-tab:${id}`, tabs[0]?.key ?? "");
  const [hash, setHash] = useState<string | null>(null);
  useEffect(() => {
    // The URL names a tab to open: on arrival, or when the app is sent to
    // another tab of the page it's on (the tray's Check for updates).
    // #connections/acme-info: the tab, and something in it (see connectionHref).
    const follow = () => {
      const fromUrl = window.location.hash.slice(1).split("/")[0] ?? "";
      if (tabs.some((t) => t.key === fromUrl)) setHash(fromUrl);
    };
    follow();
    window.addEventListener("hashchange", follow);
    return () => window.removeEventListener("hashchange", follow);
  }, []);
  const active = tabs.find((t) => t.key === (hash ?? stored)) ?? tabs[0];
  function open(key: string) {
    setHash(null);
    setStored(key);
    try {
      window.history.replaceState(null, "", `#${key}`);
    } catch {
      // nothing to do
    }
  }
  if (!active) return null;
  return (
    <div>
      {/* The line is a shadow under the tabs, not a border they overlap: overlapping
          it overflowed the row by a pixel, which showed a scroll bar. Too many tabs
          for the width go on a second row: scrolling sideways hid the last ones
          with nothing to say they were there. */}
      <div role="tablist" className="mb-4 flex flex-wrap gap-x-1 shadow-[inset_0_-1px_0_var(--color-line)]">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === active.key}
            onClick={() => open(t.key)}
            className={`min-h-11 shrink-0 border-b-2 px-2 text-sm font-medium ${t.key === active.key ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}
          >
            {t.label}
            {t.count !== undefined ? <span className="ml-1.5 rounded-full bg-sunken px-1.5 text-xs text-muted">{t.count}</span> : null}
          </button>
        ))}
      </div>
      <div role="tabpanel">{active.content()}</div>
    </div>
  );
}

/**
 * A section that folds away, showing `summary` (one line, e.g. "4 · 1 production")
 * while folded. `id` is unique on the page; open or folded is remembered per page.
 */
export function Section({ id, title, summary, action, defaultOpen = true, children }: { id: string; title: string; summary?: ReactNode; action?: ReactNode; defaultOpen?: boolean; children: ReactNode }) {
  const [page, setPage] = useState("");
  useEffect(() => setPage(window.location.pathname + window.location.search), []);
  const [state, setState] = useRemembered(`rk-section:${page}:${id}`, defaultOpen ? "open" : "closed");
  const { named } = useNamedHere(id);
  useEffect(() => {
    // A link to it (Ctrl+K) opens it. Again once `page` is known: the remembered
    // "folded" is read then, and would close it again.
    if (named) setState("open");
  }, [named, page]);
  const open = state === "open";
  return (
    <section id={`section-${id}`} className="mb-4 scroll-mt-4 rounded-2xl border border-line bg-surface">
      <div className="flex items-center gap-2 p-3 sm:px-4">
        <button type="button" onClick={() => setState(open ? "closed" : "open")} aria-expanded={open} className="flex min-h-9 min-w-0 flex-1 items-center gap-2 text-left">
          <span aria-hidden className={`inline-block text-muted transition-transform ${open ? "rotate-90" : ""}`}>
            ›
          </span>
          <h2 className="font-semibold">{title}</h2>
          {summary ? <span className="truncate text-sm text-muted">· {summary}</span> : null}
        </button>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
      {open ? <div className="space-y-3 border-t border-line p-3 sm:p-4">{children}</div> : null}
    </section>
  );
}

/** One line of a long list; its details open below it. */
export function CompactRow({ head, children, defaultOpen = false }: { head: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-line bg-surface">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="flex min-h-11 w-full items-center gap-2 px-3 py-2 text-left text-sm">
        <span aria-hidden className={`inline-block text-muted transition-transform ${open ? "rotate-90" : ""}`}>
          ›
        </span>
        <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">{head}</div>
      </button>
      {open ? <div className="space-y-3 border-t border-line p-3">{children}</div> : null}
    </div>
  );
}
