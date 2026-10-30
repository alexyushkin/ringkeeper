"use client";

import Link from "@/components/link";
import { useEffect, useId, useState, type ReactNode } from "react";

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
};

export function StatusBadge({ status }: { status: string }) {
  const [tone, label] = STATUS[status] ?? ["neutral", status];
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

export function LinkButton({ href, children, kind = "secondary" }: { href: string; children: ReactNode; kind?: "primary" | "secondary" }) {
  const styles = kind === "primary" ? "bg-accent text-accent-ink" : "bg-surface border border-line text-ink";
  return (
    <Link href={href} className={`inline-flex min-h-11 items-center justify-center rounded-xl px-4 text-sm font-medium ${styles}`}>
      {children}
    </Link>
  );
}

export function Spinner() {
  return <span aria-hidden className="inline-block size-4 animate-spin rounded-full border-2 border-current border-r-transparent" />;
}

export function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <section className={`rounded-2xl border border-line bg-surface p-4 ${className}`}>{children}</section>;
}

export function PageHeader({ title, subtitle, action }: { title: string; subtitle?: string; action?: ReactNode }) {
  return (
    <header className="mb-4 flex items-start justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold">{title}</h1>
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
        className="min-h-11 w-full rounded-xl border border-line bg-surface px-3 text-base outline-none focus:border-accent"
      />
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function TextArea({ label, value, onChange, rows = 3, hint }: { label: string; value: string; onChange: (v: string) => void; rows?: number; hint?: ReactNode }) {
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
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-line bg-surface px-3 py-2 text-base outline-none focus:border-accent"
      />
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
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
        className="min-h-11 w-full rounded-xl border border-line bg-surface px-3 text-base"
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
export function Details({ children, label = "Show details" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(!open)} className="text-sm text-accent" aria-expanded={open}>
        {open ? "Hide details" : label}
      </button>
      {open ? <div className="mt-2">{children}</div> : null}
    </div>
  );
}

export function Code({ children }: { children: string }) {
  return <pre className="max-h-96 overflow-auto rounded-xl bg-sunken p-3 text-xs leading-relaxed whitespace-pre-wrap break-words">{children}</pre>;
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
    const fromUrl = window.location.hash.slice(1);
    if (tabs.some((t) => t.key === fromUrl)) setHash(fromUrl);
    // Only on arrival: the URL names a tab to open.
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
      <div role="tablist" className="mb-4 flex gap-1 overflow-x-auto border-b border-line">
        {tabs.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={t.key === active.key}
            onClick={() => open(t.key)}
            className={`-mb-px min-h-11 shrink-0 border-b-2 px-3 text-sm font-medium ${t.key === active.key ? "border-accent text-accent" : "border-transparent text-muted hover:text-ink"}`}
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
  const open = state === "open";
  return (
    <section className="mb-4 rounded-2xl border border-line bg-surface">
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
