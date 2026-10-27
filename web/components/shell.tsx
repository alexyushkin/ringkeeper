"use client";

import Link from "@/components/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { ScopeSwitcher } from "@/components/scope-switcher";
import { UpdateBanner } from "@/components/update-banner";
import { Loading } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { ScopeProvider } from "@/lib/client/scope";

/* Phone: a bottom tab bar (Home, Approvals, Tasks, More). Desktop: a sidebar. */

const MAIN = [
  { href: "/", label: "Home", icon: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" },
  { href: "/approvals", label: "Approvals", icon: "M9 12l2 2 4-4M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" },
  { href: "/tasks", label: "Tasks", icon: "M4 6h16M4 12h16M4 18h10" },
];
const MORE = [
  { href: "/projects", label: "Clients & projects" },
  { href: "/connections", label: "Connections" },
  { href: "/activity", label: "Activity" },
  { href: "/settings", label: "Settings" },
];

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function active(path: string, href: string): boolean {
  return href === "/" ? path === "/" || path.startsWith("/threads") : path.startsWith(href);
}

interface Pending {
  id: string;
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [moreOpen, setMoreOpen] = useState(false);
  // The desktop app opens on first run until it's finished (server installs get
  // there through sign-in instead).
  const [ready, setReady] = useState(!DESKTOP);
  useEffect(() => {
    if (!DESKTOP) return;
    api<{ done?: boolean }>("setup/status")
      .then((status) => (status.done ? setReady(true) : router.replace("/setup")))
      .catch(() => setReady(true));
  }, [router]);
  const approvals = useApi<Pending[]>("decisions", { status: "pending", limit: 200 });
  const settings = useApi<{ values: { paused: boolean } }>("settings");
  useLive((event) => {
    if (event.type === "approval") approvals.reload();
  });
  const count = approvals.data?.length ?? 0;
  const paused = settings.data?.values.paused ?? false;

  const badge = count ? (
    <span className="ml-auto rounded-full bg-bad px-1.5 text-xs font-semibold text-white" aria-label={`${count} waiting`}>
      {count}
    </span>
  ) : null;

  if (!ready) return <Loading />;

  return (
    <ScopeProvider>
    <div className="md:flex">
      <aside className="sticky top-0 hidden h-dvh w-60 shrink-0 flex-col gap-1 border-r border-line bg-surface p-4 md:flex">
        <Link href="/" className="mb-4 flex items-center gap-2 px-2 text-lg font-semibold">
          <span aria-hidden className="inline-block size-5 rounded-full border-4 border-accent" />
          Ringkeeper
        </Link>
        {[...MAIN, ...MORE].map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`flex items-center rounded-xl px-3 py-2 text-sm ${active(path, item.href) ? "bg-accent-soft font-medium text-accent" : "text-ink hover:bg-sunken"}`}
          >
            {item.label}
            {item.href === "/approvals" ? badge : null}
          </Link>
        ))}
      </aside>

      <div className="min-w-0 flex-1">
        {paused ? (
          <div className="flex items-center justify-between gap-3 bg-bad px-4 py-2 text-sm text-white">
            <span>Everything is stopped. Nothing new starts until you resume.</span>
            <button
              type="button"
              className="rounded-lg bg-white/20 px-3 py-1 font-medium"
              onClick={() => api("resume-all", { method: "POST" }).then(settings.reload)}
            >
              Resume
            </button>
          </div>
        ) : null}
        {DESKTOP ? <UpdateBanner /> : null}
        <ScopeSwitcher />
        <main className="mx-auto w-full max-w-3xl px-4 pt-4 pb-28 md:pb-10">{children}</main>
      </div>

      <nav className="pb-safe fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t border-line bg-surface md:hidden" aria-label="Main">
        {MAIN.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            className={`relative flex flex-col items-center gap-0.5 pt-2 text-xs ${active(path, item.href) ? "text-accent" : "text-muted"}`}
          >
            <Icon d={item.icon} />
            {item.label}
            {item.href === "/approvals" && count ? (
              <span className="absolute top-1 right-[28%] rounded-full bg-bad px-1.5 text-[10px] font-semibold text-white">{count}</span>
            ) : null}
          </Link>
        ))}
        <button
          type="button"
          onClick={() => setMoreOpen(!moreOpen)}
          className={`flex flex-col items-center gap-0.5 pt-2 text-xs ${MORE.some((m) => active(path, m.href)) ? "text-accent" : "text-muted"}`}
          aria-expanded={moreOpen}
        >
          <Icon d="M5 12h.01M12 12h.01M19 12h.01" />
          More
        </button>
      </nav>
      {moreOpen ? (
        <div className="fixed inset-0 z-10 bg-black/30 md:hidden" onClick={() => setMoreOpen(false)}>
          <div className="pb-safe absolute inset-x-0 bottom-16 rounded-t-2xl bg-surface p-2" onClick={(e) => e.stopPropagation()}>
            {MORE.map((item) => (
              <Link key={item.href} href={item.href} onClick={() => setMoreOpen(false)} className="block rounded-xl px-4 py-3 text-base hover:bg-sunken">
                {item.label}
              </Link>
            ))}
          </div>
        </div>
      ) : null}
    </div>
    </ScopeProvider>
  );
}
