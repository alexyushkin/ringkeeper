"use client";

import Link from "@/components/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { CommandPalette, openPalette } from "@/components/command-palette";
import { ScopeSwitcher } from "@/components/scope-switcher";
import { startTour, Tour } from "@/components/tour";
import { UpdateBanner } from "@/components/update-banner";
import { Loading, Logo } from "@/components/ui";
import { api, useApi, useLive } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { ScopeProvider } from "@/lib/client/scope";

/* Phone: a bottom tab bar (Home, Approvals, Tasks, More). Desktop: a sidebar. */

const MAIN = [
  { href: "/", label: "Home", icon: "M3 11l9-8 9 8v9a1 1 0 0 1-1 1h-5v-6h-6v6H4a1 1 0 0 1-1-1z" },
  { href: "/approvals", label: "Approvals", icon: "M9 12l2 2 4-4M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z" },
  { href: "/tasks", label: "Tasks", icon: "M4 6h16M4 12h16M4 18h10" },
];
const CIRCLE = "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0z";
const MORE = [
  { href: "/projects", label: "Clients & projects", icon: "M4 8h16v11H4zM9 8V5h6v3M4 13h16" },
  {
    href: "/connections",
    label: "Connectors",
    icon: "M10 14a4 4 0 0 0 5.66 0l3-3a4 4 0 0 0-5.66-5.66l-1 1M14 10a4 4 0 0 0-5.66 0l-3 3a4 4 0 0 0 5.66 5.66l1-1",
  },
  { href: "/agents", label: "Agents", icon: "M12 3v3M7 8h10a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2zM9.5 13h.01M14.5 13h.01" },
  { href: "/routines", label: "Routines", icon: `${CIRCLE}M12 7v5l3 2` },
  { href: "/activity", label: "Activity", icon: "M3 12h4l3-8 4 16 3-8h4" },
  { href: "/settings", label: "Settings", icon: "M4 6h9M17 6h3M4 12h3M11 12h9M4 18h11M19 18h1M15 4v4M9 10v4M17 16v4" },
  { href: "/help", label: "Help", icon: `${CIRCLE}M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6v.6M12 17h.01` },
];
const SEARCH = "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14zM20 20l-4-4";

// What the first-run tour points at (components/tour), wherever it's on screen.
const TOUR: Record<string, string> = { "/approvals": "approvals", "/connections": "connections" };

function Icon({ d, small = false }: { d: string; small?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" className={`${small ? "size-5" : "size-6"} shrink-0`} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d={d} />
    </svg>
  );
}

function active(path: string, href: string): boolean {
  // A conversation is under Home: /threads/… on a server, /thread?id=… in the desktop app.
  return href === "/" ? path === "/" || path.startsWith("/thread") : path.startsWith(href);
}

interface Pending {
  id: string;
}

const SIDEBAR_COOKIE = "rk-sidebar";

/** The sidebar with names, or icons only. A cookie, like the theme: the desktop
 * app's address changes each launch, and this browser's storage with it. */
function useNarrowSidebar(): [boolean, (on: boolean) => void] {
  const [narrow, setNarrow] = useState(false);
  useEffect(() => {
    setNarrow(new RegExp(`(?:^|; )${SIDEBAR_COOKIE}=icons(?:;|$)`).test(document.cookie));
  }, []);
  const choose = (on: boolean) => {
    setNarrow(on);
    const secure = location.protocol === "https:" ? "; Secure" : "";
    document.cookie = `${SIDEBAR_COOKIE}=${on ? "icons" : ""}; Path=/; Max-Age=${on ? 365 * 24 * 60 * 60 : 0}; SameSite=Lax${secure}`;
  };
  return [narrow, choose];
}

export function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [moreOpen, setMoreOpen] = useState(false);
  const [narrow, setNarrow] = useNarrowSidebar();
  // The desktop app opens on first run until it's finished (server installs get
  // there through sign-in instead).
  const [ready, setReady] = useState(!DESKTOP);
  useEffect(() => {
    if (!DESKTOP) return;
    api<{ done?: boolean }>("setup/status")
      .then((status) => (status.done ? setReady(true) : router.replace("/setup")))
      .catch(() => setReady(true));
  }, [router]);
  // ?tour=1: Help's Start the tour.
  useEffect(() => {
    if (new URLSearchParams(window.location.search).has("tour")) startTour();
  }, []);
  // Approvals only: a question waits in its conversation, not in this count.
  const approvals = useApi<Pending[]>("decisions", { status: "pending", category: "approval", limit: 200 });
  const settings = useApi<{ values: { paused: boolean } }>("settings");
  const start = useApi<{ tour_seen: boolean }>(ready ? "start" : null);
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
      <aside
        className={`sticky top-0 hidden h-dvh shrink-0 flex-col gap-1 overflow-y-auto border-r border-line bg-surface md:flex ${narrow ? "w-16 items-center px-2 py-4" : "w-60 p-4"}`}
      >
        <Link href="/" className={`mb-4 flex items-center gap-2 text-lg font-semibold ${narrow ? "justify-center" : "px-2"}`} title={narrow ? "Ringkeeper" : undefined}>
          <Logo />
          {narrow ? null : "Ringkeeper"}
        </Link>
        <button
          type="button"
          onClick={openPalette}
          title={narrow ? "Search or jump to… (Ctrl K)" : undefined}
          aria-label={narrow ? "Search or jump to" : undefined}
          className={`mb-2 flex min-h-10 items-center gap-2 rounded-xl border border-line text-left text-sm text-muted hover:border-accent ${narrow ? "w-10 justify-center" : "px-3"}`}
        >
          {narrow ? (
            <Icon d={SEARCH} small />
          ) : (
            <>
              <span className="flex-1">Search or jump to…</span>
              <kbd className="rounded border border-line px-1 text-xs">Ctrl K</kbd>
            </>
          )}
        </button>
        {[...MAIN, ...MORE].map((item) => (
          <Link
            key={item.href}
            href={item.href}
            data-tour={TOUR[item.href]}
            title={narrow ? item.label : undefined}
            aria-label={narrow ? (item.href === "/approvals" && count ? `${item.label}, ${count} waiting` : item.label) : undefined}
            className={`relative flex items-center gap-3 rounded-xl py-2 text-sm ${narrow ? "w-10 justify-center" : "px-3"} ${active(path, item.href) ? "bg-accent-soft font-medium text-accent" : "text-ink hover:bg-sunken"}`}
          >
            <Icon d={item.icon} small />
            {narrow ? (
              item.href === "/approvals" && count ? <span className="absolute top-1 right-1 size-2.5 rounded-full bg-bad" aria-hidden /> : null
            ) : (
              <>
                {item.label}
                {item.href === "/approvals" ? badge : null}
              </>
            )}
          </Link>
        ))}
        <button
          type="button"
          onClick={() => setNarrow(!narrow)}
          title={narrow ? "Show the menu" : "Hide the menu"}
          aria-label={narrow ? "Show the menu" : "Hide the menu"}
          aria-expanded={!narrow}
          className="mt-auto grid size-10 place-items-center rounded-xl text-muted hover:bg-sunken hover:text-ink"
        >
          <Icon d="M5 5h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V6a1 1 0 0 1 1-1zM9 5v14" small />
        </button>
      </aside>

      <div className="min-w-0 flex-1">
        {paused ? (
          <div className="flex items-center justify-between gap-3 bg-bad px-4 py-2 text-sm text-white">
            <span>Everything is stopped. Nothing new starts until you resume.</span>
            <button
              type="button"
              className="rounded-lg bg-white/20 px-3 py-1 font-medium"
              onClick={() => api("resume-all", { method: "POST" }).then(settings.reload, () => window.alert("Couldn't resume. Try again in a moment, or use Resume in Settings."))}
            >
              Resume
            </button>
          </div>
        ) : null}
        {DESKTOP ? <UpdateBanner /> : null}
        <ScopeSwitcher />
        {/* As wide as the window allows, up to where lines get too long to read. */}
        <main className="mx-auto w-full max-w-[96rem] px-4 pt-4 pb-28 md:px-6 md:pb-10 xl:px-10">{children}</main>
      </div>

      <nav className="pb-safe fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t border-line bg-surface md:hidden" aria-label="Main">
        {MAIN.map((item) => (
          <Link
            key={item.href}
            href={item.href}
            data-tour={TOUR[item.href]}
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
          data-tour="connections"
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
            <button type="button" onClick={() => { setMoreOpen(false); openPalette(); }} className="block w-full rounded-xl px-4 py-3 text-left text-base hover:bg-sunken">
              Search or jump to…
            </button>
            {MORE.map((item) => (
              <Link key={item.href} href={item.href} onClick={() => setMoreOpen(false)} className="block rounded-xl px-4 py-3 text-base hover:bg-sunken">
                {item.label}
              </Link>
            ))}
          </div>
        </div>
      ) : null}
      <CommandPalette />
      {start.data ? <Tour auto={!start.data.tour_seen && path === "/"} onSeen={start.reload} /> : null}
    </div>
    </ScopeProvider>
  );
}
