"use client";

import { useEffect, useState } from "react";

import { ConnectRemote, type RemoteService } from "@/components/connection-library";
import { ConnectionForm, defaultTitle, EnvChoice, nameFor, useTakenNames } from "@/components/connection-form";
import { GoogleConnect } from "@/components/google";
import { VendorConnect, type VendorInfo } from "@/components/vendor-connect";
import { KindChoice, type Kind } from "@/components/shared-tools";
import { Alert, Badge, Button, Details, Field, Select, Spinner, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { LOGO_FILES, LOGOS } from "@/lib/client/logos";
import { projectName } from "@/lib/client/scope";

/**
 * The connectors: built-in services first (Ringkeeper's own rules: undo, SQL checks,
 * approvals), each a tile that opens its form; then services you sign in to; then
 * any other connector from the MCP Registry (searchable, and from a cache when
 * offline: the service's own first, community ones folded away), by its address,
 * or a desktop extension from its file. Every connection belongs to `client`, or is
 * a shared tool (yours, for any client's projects); with `project`, it's linked to
 * that project. With `kind`, that was asked up front; otherwise each server asks,
 * suggesting from what it needs.
 */

interface Header {
  name: string;
  description: string;
  required: boolean;
  secret: boolean;
  value: string;
}

interface Input {
  name: string;
  description: string;
  required: boolean;
  secret: boolean;
  default: string;
  value: string;
  type: string;
}

interface RemoteEntry {
  type: string;
  url: string;
  headers: Header[];
  usable: boolean;
}

interface PackageEntry {
  registry: string;
  identifier: string;
  version: string;
  env: Input[];
  arguments: Input[];
  usable: boolean;
}

export interface CatalogServer {
  name: string;
  title: string;
  description: string;
  version: string;
  publisher: string;
  verified: "domain" | "github";
  status: string;
  website: string;
  repository: string;
  kind: string;
  remotes: RemoteEntry[];
  packages: PackageEntry[];
  signin: boolean | null;
  recommended: string | null;
}

interface SearchResult {
  servers: CatalogServer[];
  next_cursor: string | null;
  offline: boolean;
  cached_at: number | null;
}

interface Plan {
  server: string;
  publisher: string;
  verified: string;
  registry: string;
  identifier: string;
  version: string;
  install: string;
  runs: string;
  needs: string;
  missing: boolean;
  env: Input[];
  arguments: Input[];
  fingerprint: string;
}

interface ExtensionSetting {
  key: string;
  title: string;
  description: string;
  type: string;
  required: boolean;
  secret: boolean;
  default: string;
  multiple: boolean;
}

interface Extension {
  fingerprint: string;
  name: string;
  title: string;
  version: string;
  description: string;
  publisher: string;
  kind: string;
  tools: { name: string; description: string }[];
  tools_generated: boolean;
  settings: ExtensionSetting[];
  runs: string;
  problem: string | null;
  size: number;
  files: number;
}

interface Target {
  client: string;
  project?: string;
  kind?: Kind; // already chosen: a client's account, or (shared) your own tool
  onAdded: (name: string) => void;
}

/** "Notion · General Informatics LLC" for a client's; your own tool by itself. */
const titleFor = (kind: Kind, client: string, service: string) => defaultTitle(service, "", kind === "shared" ? undefined : client);

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** Wait for a background install (a package or an extension) to finish. */
async function installed(name: string): Promise<void> {
  const deadline = Date.now() + 15 * 60_000;
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    const job = await api<{ state: string; error: string | null }>(`connections/local/${name}`);
    if (job.state === "done") return;
    if (job.state === "failed") throw new Error(job.error ?? "The install didn't finish. Try again; if it fails again, check the internet connection or pick another tool.");
    if (Date.now() > deadline) throw new Error("The install is still running after 15 minutes. Look for it on the Connections page later; if it isn't there, try again.");
  }
}

export function Verified({ server }: { server: { publisher: string; verified: string } }) {
  const domain = server.verified === "domain";
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-xs text-muted" title={domain ? "The MCP Registry checked that the publisher controls this domain." : "The MCP Registry checked that the publisher signed in as this GitHub account. Anyone can have one."}>
      by <span className="font-medium text-ink">{server.publisher}</span>
      {domain ? <Badge tone="good">✓ verified domain</Badge> : <Badge>GitHub account</Badge>}
    </span>
  );
}

function SignInBadge({ signin }: { signin: boolean | null }) {
  if (signin === true) return <Badge tone="good">one-click sign-in</Badge>;
  if (signin === false) return <Badge>needs a key</Badge>;
  return null;
}

/** What people look for, in the order the catalog shows it. */
const CATEGORIES = ["Databases", "Email", "CRM", "Accounting", "Payments", "CMS", "Social", "Ads", "Cloud", "Storage", "AI", "Dev tools", "Other"] as const;
type Category = (typeof CATEGORIES)[number];

/** A built-in service: its own form (a ConnectionForm type, Google, or a token service). */
interface Tile {
  key: string;
  label: string;
  sub: string;
  open: "form" | "google" | "remote";
  words: string[]; // what a search for it might say
  badge?: string; // instead of "Built in"
  category: Category;
}

const BUILT_IN: Tile[] = [
  { key: "supabase", label: "Database", sub: "Postgres: Supabase, Neon or your own", open: "form", category: "Databases", words: ["postgres", "postgresql", "sql", "supabase", "neon", "database", "db"] },
  { key: "airtable", label: "Airtable", sub: "Bases and records, with undo", open: "form", category: "Databases", words: ["airtable"] },
  { key: "oracle", label: "Oracle Database", sub: "Your own, or Autonomous with a wallet", open: "form", category: "Databases", words: ["oracle", "oracledb", "sql", "autonomous", "atp", "adb", "wallet", "database"] },
  { key: "mailbox", label: "Mailbox", sub: "Gmail, Outlook, Zoho, Yahoo or any IMAP", open: "form", category: "Email", words: ["mailbox", "gmail", "outlook", "office365", "microsoft", "zoho", "yahoo", "imap", "email", "mail", "inbox"] },
  { key: "smtp", label: "Email sending", sub: "SMTP, any provider", open: "form", category: "Email", words: ["smtp", "email", "mail", "send", "sendgrid", "mailgun", "postmark", "brevo", "ses", "mailchimp", "mandrill"] },
  { key: "resend", label: "Resend", sub: "Email sending", open: "form", category: "Email", words: ["resend", "email", "mail", "send"] },
  { key: "google", label: "Google account", sub: "Gmail, Drive, Calendar, Sheets, Docs, YouTube, Search Console, Analytics", open: "google", category: "Email", words: ["google", "gmail", "drive", "calendar", "sheets", "docs", "analytics", "ga4", "gsc", "seo", "search console", "youtube", "videos", "social"] },
  { key: "twilio", label: "Twilio", sub: "Texts and calls", open: "form", category: "Email", words: ["twilio", "sms", "text", "texts", "call", "calls"] },
  { key: "s3", label: "File storage", sub: "AWS S3, Cloudflare R2, Vercel Blob", open: "form", category: "Storage", words: ["s3", "r2", "blob", "bucket", "aws", "amazon", "minio", "backblaze", "wasabi", "spaces", "files", "storage"] },
  { key: "retell", label: "Retell AI", sub: "Voice agents", open: "remote", category: "AI", words: ["retell", "voice", "phone"] },
  { key: "github", label: "GitHub", sub: "Code, issues, pull requests", open: "form", category: "Dev tools", words: ["github", "git", "code", "repo"] },
  { key: "vercel", label: "Vercel", sub: "Deployments", open: "form", category: "Dev tools", words: ["vercel", "deploy", "hosting"] },
  { key: "n8n", label: "n8n", sub: "Automations", open: "form", category: "Dev tools", words: ["n8n", "workflow", "workflows", "automation"] },
  { key: "http", label: "Any API", sub: "An address and a key", open: "form", category: "Other", words: ["api", "rest", "http", "https", "openapi", "swagger", "webhook"] },
  { key: "formsonfire", label: "Forms On Fire", sub: "Forms and data sources", open: "form", category: "Other", badge: "Unverified", words: ["forms", "formsonfire", "fof", "appenate", "truecontext"] },
  { key: "dataforseo", label: "DataForSEO", sub: "SEO data", open: "form", category: "Other", words: ["dataforseo", "seo", "keywords", "serp", "backlinks"] },
];
// Sign-in services left off the shelf: only when a built-in tile opens that same service
// (Retell), or the server isn't offered at all (GitHub: shelf=False in remote.py). A service
// with both its own server and a built-in connection shows both, each saying what it's for
// (Airtable). tests/test_catalog.py checks every name here against remote.py.
const NOT_SIGN_IN = new Set(["retell", "github"]);
// What a service's own server is for, when the built-in tile is there too.
const SIGN_IN_SUB: Record<string, string> = {
  airtable: "Automations and interfaces: Airtable's own server",
  meta_ads: "Meta's own server, free",
  pipeboard: "A third party, with a free tier of 30 calls a week",
};
// Not the service's own server: said instead of "Official".
const SIGN_IN_BADGE: Record<string, string> = {
  pipeboard: "Third party",
};
// Where the services you sign in to belong.
const SIGN_IN_CATEGORY: Record<string, Category> = {
  neon: "Databases",
  supabase: "Databases",
  cloudflare: "Cloud",
  sentry: "Dev tools",
  linear: "Dev tools",
  atlassian: "Dev tools",
  meta_ads: "Ads",
  pipeboard: "Ads",
};

// What else a search for a service you sign in to might say.
const SIGN_IN_WORDS: Record<string, string[]> = {
  calcom: ["calendar", "booking", "bookings", "scheduling", "schedule", "meetings", "appointments"],
  airtable: ["automations", "automation", "interfaces", "interface"],
  meta_ads: ["facebook", "instagram", "advertising", "campaigns", "campaign"],
  pipeboard: ["facebook", "instagram", "advertising", "campaigns", "campaign"],
};

// Services with no logo in lib/client/logos or public/logos: their initials on their color.
const MARKS: Record<string, [string, string, string]> = {
  twilio: ["T", "#f22f46", "#fff"],
  retell: ["R", "#6d28d9", "#fff"],
  mailbox: ["@", "#2563eb", "#fff"],
  smtp: ["@", "#0f766e", "#fff"],
  http: ["{}", "#475569", "#fff"],
  s3: ["S3", "#c2410c", "#fff"],
  formsonfire: ["F", "#dc2626", "#fff"],
  dataforseo: ["D", "#1d4ed8", "#fff"],
  pagespeed: ["PS", "#1a73e8", "#fff"],
  apify: ["Ap", "#97d700", "#111"],
  apollo: ["Ap", "#ebf212", "#111"],
  attio: ["At", "#1c1d1f", "#fff"],
  aws: ["aws", "#232f3e", "#ff9900"],
  azure: ["Az", "#0078d4", "#fff"],
  bill: ["B", "#ff6a3d", "#111"],
  businesscentral: ["BC", "#00b7c3", "#111"],
  close: ["C", "#2563eb", "#fff"],
  clover: ["C", "#228800", "#fff"],
  freshbooks: ["F", "#0075dd", "#fff"],
  gocardless: ["GC", "#f1f252", "#111"],
  gohighlevel: ["HL", "#188bf6", "#fff"],
  helcim: ["H", "#0f9ce5", "#fff"],
  housecallpro: ["HP", "#0a6cf5", "#fff"],
  jobber: ["J", "#7db00e", "#111"],
  keap: ["K", "#36a635", "#fff"],
  leadfeeder: ["L", "#00a36c", "#fff"],
  microsoft: ["M", "#0078d4", "#fff"],
  mollie: ["M", "#111111", "#fff"],
  monday: ["m", "#ff3d57", "#fff"],
  oci: ["O", "#c74634", "#fff"],
  oracle: ["O", "#c74634", "#fff"],
  pipedrive: ["P", "#017737", "#fff"],
  plaid: ["P", "#111111", "#fff"],
  salesforce: ["S", "#00a1e0", "#fff"],
  sanity: ["S", "#f03e2f", "#fff"],
  servicetitan: ["ST", "#0f5bff", "#fff"],
  tavily: ["T", "#2d6cdf", "#fff"],
  wave: ["W", "#1c5cff", "#fff"],
};

/** A service's logo on its color, else its initials. A database connection: Postgres's. */
export function ServiceMark({ id, database = false }: { id: string; database?: boolean }) {
  const file = database ? undefined : LOGO_FILES[id];
  if (file) {
    // The brand's own file, only scaled, on a plate that gives it its clear space.
    const size = file.wide ? "h-9 w-16" : "size-9";
    return (
      <span className={`logo-plate ${file.dark ? "logo-themed" : ""} ${file.bare ? "" : "rounded-lg border border-line"} grid ${size} shrink-0 place-items-center p-1`} aria-hidden>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={`/logos/${id}.svg`} alt="" className="logo-light size-full object-contain" />
        {file.dark ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={`/logos/${id}-dark.svg`} alt="" className="logo-dark size-full object-contain" />
        ) : null}
      </span>
    );
  }
  const logo = LOGOS[database ? "postgres" : id];
  if (logo) {
    return (
      <span className="grid size-9 shrink-0 place-items-center rounded-lg" style={{ background: logo.color, color: logo.ink }} aria-hidden>
        <svg viewBox="0 0 24 24" className="size-5" fill="currentColor">
          <path d={logo.path} />
        </svg>
      </span>
    );
  }
  const [text, background, color] = MARKS[id] ?? [id.slice(0, 1).toUpperCase(), "var(--color-sunken)", "var(--color-ink)"];
  return (
    <span className="grid size-9 shrink-0 place-items-center rounded-lg text-xs font-bold" style={{ background, color }} aria-hidden>
      {text}
    </span>
  );
}

function TileButton({ mark, label, sub, badge, onClick, database = false }: { mark: string; label: string; sub: string; badge: string; onClick: () => void; database?: boolean }) {
  return (
    <button type="button" onClick={onClick} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3 text-left text-sm hover:border-accent">
      <ServiceMark id={mark} database={database} />
      <span className="min-w-0 flex-1">
        <span className="flex flex-wrap items-center gap-x-2">
          <span className="font-medium">{label}</span>
          <Badge tone={badge === "Built in" ? "good" : badge === "Unverified" ? "warn" : undefined}>{badge}</Badge>
        </span>
        <span className="block truncate text-xs text-muted">{sub}</span>
      </span>
    </button>
  );
}

type Opened = { tile: Tile } | { service: RemoteService } | { vendor: VendorInfo };

export function Catalog({ start, startScope, ...target }: Target & { start?: string; startScope?: Record<string, string> }) {
  const services = useApi<RemoteService[]>("remote-services");
  const vendors = useApi<VendorInfo[]>("vendors");
  const [opened, setOpened] = useState<Opened | null>(null);
  // A link to one service's own server (?service=airtable), a business system (google_ads) or
  // a built-in kind (gmail): its form, once the lists are in.
  const [started, setStarted] = useState(false);
  useEffect(() => {
    if (!start || started || !services.data || !vendors.data) return;
    const service = services.data.find((s) => s.key === start);
    const vendor = vendors.data.find((v) => v.key === start);
    const tile = BUILT_IN.find((t) => t.key === start);
    if (service) setOpened({ service });
    else if (vendor) setOpened({ vendor });
    else if (tile) setOpened({ tile });
    setStarted(true);
  }, [start, started, services.data, vendors.data]);
  const [searching, setSearching] = useState(false);
  const shared = target.kind === "shared";
  const signIn = (services.data ?? []).filter((s) => !NOT_SIGN_IN.has(s.key));
  // A tile's id says which kind it is: "tile:airtable" (built in) or "service:airtable"
  // (Airtable's own server) are two different things with the same key.
  const openId = (id: string) => {
    const [kind, key] = id.split(":", 2) as [string, string];
    const tile = kind === "tile" ? BUILT_IN.find((t) => t.key === key) : undefined;
    const service = kind === "service" ? (services.data ?? []).find((s) => s.key === key) : undefined;
    const vendor = kind === "vendor" ? (vendors.data ?? []).find((v) => v.key === key) : undefined;
    if (tile) setOpened({ tile });
    else if (service) setOpened({ service });
    else if (vendor) setOpened({ vendor });
  };

  if (opened) {
    const back = (
      <Button kind="ghost" onClick={() => setOpened(null)}>
        ‹ All connectors
      </Button>
    );
    const vendor = "vendor" in opened ? opened.vendor : null;
    const service = "service" in opened ? opened.service : "tile" in opened && opened.tile.open === "remote" ? (services.data ?? []).find((s) => s.key === opened.tile.key) : undefined;
    const tile = "tile" in opened ? opened.tile : null;
    return (
      <div className="space-y-3">
        {back}
        <div className="flex items-center gap-3">
          <ServiceMark id={tile?.key ?? service?.key ?? vendor?.key ?? ""} database={tile?.key === "supabase"} />
          <h3 className="font-semibold">Connect {tile?.label ?? service?.name ?? vendor?.name}</h3>
        </div>
        {vendor ? (
          <VendorConnect vendor={vendor} client={target.client} project={target.project} shared={shared} onAdded={target.onAdded} />
        ) : tile?.open === "form" ? (
          <ConnectionForm client={shared ? undefined : target.client} project={target.project} shared={shared} type={tile.key} onSaved={(c) => target.onAdded(c.name)} />
        ) : tile?.open === "google" ? (
          <GoogleConnect owner={shared ? undefined : target.client} shared={shared} project={target.project} onAdded={target.onAdded} />
        ) : service ? (
          <ConnectRemote service={service} client={target.client} project={target.project} shared={shared} onAdded={target.onAdded} startScope={service.key === start ? startScope : undefined} />
        ) : (
          <Spinner />
        )}
      </div>
    );
  }

  // Every service, by what people look for: built in (Ringkeeper's own rules) and the
  // service's own server you sign in to.
  const tiles = [
    ...BUILT_IN.map((tile) => ({ id: `tile:${tile.key}`, key: tile.key, label: tile.label, sub: tile.sub, category: tile.category, badge: tile.badge ?? "Built in", words: tile.words, open: () => setOpened({ tile }) })),
    ...signIn.map((service) => ({
      id: `service:${service.key}`,
      key: service.key,
      label: service.name,
      sub: SIGN_IN_SUB[service.key] ?? (service.needs || (service.url_label ? "Your own address" : service.signin ? "Sign in" : "Paste a token")),
      category: SIGN_IN_CATEGORY[service.key] ?? ((service.category || "Other") as Category),
      badge: SIGN_IN_BADGE[service.key] ?? "Official",
      words: [service.key, ...service.name.toLowerCase().split(/[^a-z0-9]+/), ...(SIGN_IN_WORDS[service.key] ?? [])],
      open: () => setOpened({ service }),
    })),
    ...(vendors.data ?? []).map((vendor) => ({
      id: `vendor:${vendor.key}`,
      key: vendor.key,
      label: vendor.name,
      sub: vendor.sub,
      category: vendor.category as Category,
      badge: vendor.unverified ? "Unverified" : "Built in",
      words: [vendor.key, ...vendor.words],
      open: () => setOpened({ vendor }),
    })),
  ];
  return (
    <div className="space-y-5">
      {shared ? (
        <p className="text-sm text-muted">
          Yours, not a client&apos;s: a browser, files on this computer, a public docs server, or your own account with a service. Any client&apos;s projects can use it, or all of them.
        </p>
      ) : null}
      <CatalogSearch {...target} featured={tiles} onFeatured={openId} onTyped={setSearching} />
      {searching ? null : (
        <>
          <p className="text-xs text-muted">
            <b>Built in</b>: Ringkeeper&apos;s own rules, and most changes can be undone. <b>Official</b>: the service&apos;s own server; you sign in on its page, and a project&apos;s link can often be narrowed.
          </p>
          {CATEGORIES.map((category) => {
            const here = tiles.filter((tile) => tile.category === category);
            return here.length ? (
              <section key={category} className="space-y-2">
                <h3 className="font-medium">{category}</h3>
                <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {here.map((tile) => (
                    <TileButton key={tile.id} mark={tile.key} database={tile.key === "supabase" && tile.badge === "Built in"} label={tile.label} sub={tile.sub} badge={tile.badge} onClick={tile.open} />
                  ))}
                </div>
              </section>
            ) : null;
          })}
          {services.error ? <Alert>{services.error}</Alert> : null}
        </>
      )}
      <Details label="Add a server by its address">
        <AddByUrl {...target} services={services.data ?? []} onOpen={openId} />
      </Details>
      <Details label="Install extension (.mcpb file)">
        <InstallExtension {...target} />
      </Details>
    </div>
  );
}

interface Featured {
  id: string; // "tile:airtable", "service:airtable": the key alone can be both
  key: string;
  label: string;
  sub: string;
  words: string[];
  badge: string;
}

const squash = (text: string) => text.toLowerCase().replace(/[^a-z0-9]+/g, "");

// Services whose own connector isn't published from a domain named after them.
const OFFICIAL: Record<string, string[]> = {
  playwright: ["github.com/microsoft", "microsoft.com"],
  github: ["github.com/github"],
};

/** A server called just "mcp" reads as its publisher: stripe.com's is "Stripe". */
function serverTitle(server: CatalogServer): string {
  if (!/^(mcp|server|mcp[- ]server)$/i.test(server.title.trim())) return server.title;
  const name = server.publisher.split(/[./]/).find((part) => !["www", "mcp", "api", "github"].includes(part)) ?? server.title;
  return `${name.charAt(0).toUpperCase()}${name.slice(1)}`;
}

/** One row per server: the same title from the same publisher shows once. */
function dedupe(servers: CatalogServer[]): CatalogServer[] {
  const seen = new Set<string>();
  return servers.filter((server) => {
    const key = `${squash(server.title)}|${server.publisher.toLowerCase()}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function CatalogSearch({ featured = [], onFeatured, onTyped, ...target }: Target & { featured?: Featured[]; onFeatured?: (key: string) => void; onTyped?: (searching: boolean) => void }) {
  const [typed, setTyped] = useState("");
  const [query, setQuery] = useState("");
  const [pages, setPages] = useState<SearchResult[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chosen, setChosen] = useState<CatalogServer | null>(null);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(typed.trim()), 350);
    return () => clearTimeout(timer);
  }, [typed]);

  useEffect(() => {
    // Nothing until there's something to look for: the catalog is thousands of servers.
    if (!query) {
      setPages([]);
      setBusy(false);
      return;
    }
    let cancelled = false;
    setBusy(true);
    setError(null);
    api<SearchResult>("catalog", { query: { q: query } })
      .then((page) => !cancelled && setPages([page]))
      .catch((err: unknown) => !cancelled && setError(errorText(err)))
      .finally(() => !cancelled && setBusy(false));
    return () => {
      cancelled = true;
    };
  }, [query]);

  async function more() {
    const last = pages[pages.length - 1];
    if (!last?.next_cursor) return;
    setBusy(true);
    try {
      const page = await api<SearchResult>("catalog", { query: { q: query, cursor: last.next_cursor } });
      setPages([...pages, page]);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  // Services with built-in rules are tiles; of the rest, the service's own come first:
  // published from a domain the search names (stripe.com for "stripe"), or known to be.
  const servers = dedupe(pages.flatMap((p) => p.servers)).filter((server) => !server.recommended);
  const words = query.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length >= 2);
  const own = (server: CatalogServer) => {
    const publisher = server.publisher.toLowerCase();
    if (words.some((w) => OFFICIAL[w]?.includes(publisher))) return true;
    if (server.verified !== "domain") return false;
    return words.length ? words.some((w) => publisher.split(".").includes(w)) : true;
  };
  const official = servers.filter(own);
  const community = servers.filter((server) => !own(server));
  const matches = words.length ? featured.filter((f) => words.some((w) => f.words.some((known) => known.startsWith(w)) || squash(f.label).startsWith(w))) : [];
  const last = pages[pages.length - 1];
  const row = (server: CatalogServer) => (
    <li key={server.name}>
      <button type="button" aria-expanded={chosen?.name === server.name} onClick={() => setChosen(chosen?.name === server.name ? null : server)} className={`w-full rounded-xl border px-3 py-2 text-left text-sm hover:border-accent ${chosen?.name === server.name ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}>
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{serverTitle(server)}</span>
          {server.kind.includes("remote") ? <Badge>remote</Badge> : null}
          {server.kind.includes("local") ? <Badge>runs on this computer</Badge> : null}
          <SignInBadge signin={server.signin} />
          <Badge>{own(server) ? "Official" : "Community"}</Badge>
          {server.status === "deprecated" ? <Badge tone="warn">deprecated</Badge> : null}
        </span>
        <span className="mt-0.5 block">
          <Verified server={server} />
        </span>
        {chosen?.name === server.name ? (
          <>
            <span className="mt-1 block text-muted">{server.description}</span>
            <span className="mt-1 block text-xs text-muted">{server.name}</span>
          </>
        ) : (
          <span className="mt-0.5 block truncate text-muted">{server.description}</span>
        )}
      </button>
      {chosen?.name === server.name ? <ServerConnect key={server.name} server={server} {...target} /> : null}
    </li>
  );
  return (
    <div className="space-y-2">
      <Field
        label="Search connectors"
        value={typed}
        onChange={(text) => {
          setTyped(text);
          onTyped?.(Boolean(text.trim()));
        }}
        placeholder="gmail, postgres, s3, playwright…"
      />
      {matches.length ? (
        <ul className="space-y-2" aria-label="Built in">
          {matches.map((f) => (
            <li key={f.id}>
              <button type="button" onClick={() => onFeatured?.(f.id)} className="flex w-full items-center gap-3 rounded-xl border border-accent bg-accent-soft px-3 py-2 text-left text-sm">
                <ServiceMark id={f.key} database={f.key === "supabase"} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{f.label}</span>
                    <Badge tone={f.badge === "Built in" ? "good" : f.badge === "Unverified" ? "warn" : undefined}>{f.badge}</Badge>
                  </span>
                  <span className="block truncate text-xs text-muted">{f.sub}</span>
                </span>
                <span className="text-sm text-accent">Connect ›</span>
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {last?.offline ? <Alert tone="warn">Offline: showing servers seen before{last.cached_at ? ` (as of ${new Date(last.cached_at * 1000).toLocaleDateString()})` : ""}.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {official.length ? (
        <>
          {words.length ? <h4 className="pt-1 text-xs font-semibold text-muted uppercase">From the service</h4> : null}
          <ul className="space-y-2">{official.map(row)}</ul>
        </>
      ) : null}
      {community.length ? (
        <div className="space-y-1 pt-1">
          <p className="text-xs text-muted">Community servers are published by someone other than the service. Check who publishes one before you connect it.</p>
          <Details label={`Community servers (${community.length})`} defaultOpen={!official.length && !matches.length}>
            <ul className="space-y-2">{community.map(row)}</ul>
          </Details>
        </div>
      ) : null}
      {query && !busy && !servers.length && !matches.length && !error ? <p className="text-sm text-muted">Nothing found.</p> : null}
      {busy ? <Spinner /> : null}
      {last?.next_cursor && !busy ? (
        <Button kind="ghost" onClick={more}>
          More
        </Button>
      ) : null}
    </div>
  );
}

/** The name, and (a client's: your own tool holds no client's data) the kind of data. */
function ConnectionBasics({ title, setTitle, env, setEnv, shared }: { title: string; setTitle: (v: string) => void; env: string; setEnv: (v: string) => void; shared: boolean }) {
  return (
    <>
      {shared ? null : <EnvChoice value={env} onChange={setEnv} tool />}
      <Field label="Name" value={title} onChange={setTitle} hint="As it shows in Ringkeeper. Change it if you like." />
    </>
  );
}

/** A catalog server: connect to its remote, or install its package. */
function ServerConnect({ server, client, project, kind, onAdded }: { server: CatalogServer } & Target) {
  const remote = server.remotes.find((r) => r.usable);
  const packageIndex = server.packages.findIndex((p) => p.usable);
  const [how, setHow] = useState<"remote" | "local">(remote ? "remote" : "local");
  const services = useApi<{ key: string; name: string }[]>("remote-services");
  if (server.recommended) {
    const known = services.data?.find((s) => s.key === server.recommended);
    return <Alert tone="good">This is {known?.name ?? server.recommended}, which has built-in rules: connect it from Recommended above.</Alert>;
  }
  if (!remote && packageIndex < 0) {
    return <Alert tone="warn">Ringkeeper can&apos;t connect this one yet: it has no remote address it can use and no npm, PyPI or container package that runs over stdio.</Alert>;
  }
  return (
    <div className="mt-2 space-y-3 rounded-xl border border-line p-3">
      {remote && packageIndex >= 0 ? (
        <Select label="How" value={how} onChange={(v) => setHow(v as "remote" | "local")} options={[["remote", "Connect to its server"], ["local", "Install it on this computer"]]} />
      ) : null}
      {how === "remote" && remote ? (
        <RemoteConnect url={remote.url} server={server.name} headers={remote.headers} signin={server.signin} title={server.title} client={client} project={project} kind={kind} onAdded={onAdded} />
      ) : (
        <LocalInstall server={server} index={packageIndex} client={client} project={project} kind={kind} onAdded={onAdded} />
      )}
    </div>
  );
}

/**
 * A remote server without built-in rules: sign in, give it the token or headers it
 * needs, or (a server that needs nothing) just connect. Signing in or a key
 * suggests a client's account; needing nothing suggests your own tool.
 */
function RemoteConnect({ url, server, headers, signin, open: probed, title, client, project, kind: fixed, onAdded }: { url: string; server?: string; headers: Header[]; signin: boolean | null; open?: boolean; title: string } & Target) {
  // Headers to fill in: no value, or a value with a {placeholder} in it.
  const named = headers.filter((h) => !h.value || h.value.includes("{"));
  const [open, setOpen] = useState<boolean | null>(probed ?? (named.some((h) => h.required) ? false : null));
  const [kind, setKind] = useState<Kind>(fixed ?? (open ? "shared" : "client"));
  const [all, setAll] = useState(false);
  const [typed, setTyped] = useState<string | null>(null); // null: the suggested title
  const [env, setEnv] = useState("");
  const [token, setToken] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [pasting, setPasting] = useState(signin === false && !open);
  const [waiting, setWaiting] = useState<string | null>(null); // the name sent, until it's there
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shared = kind === "shared";
  const taken = useTakenNames();
  const shown = typed ?? titleFor(kind, client, title);
  const name = nameFor(shown, shared ? undefined : client, taken);
  const data = shared ? "dev" : env; // your own tool holds no client's data
  const body = { url, server: server ?? null, name, title: shown.trim(), owner: client, env: data, project: project ?? null, shared, all_projects: shared && all };

  useEffect(() => {
    // From the catalog: ask the server whether it needs anything at all.
    if (open !== null) return;
    let cancelled = false;
    api<{ open: boolean }>("catalog/probe", { json: { url } })
      .then((found) => {
        if (cancelled) return;
        setOpen(found.open);
        if (found.open) setPasting(false);
        if (found.open && !fixed) setKind("shared");
      })
      .catch(() => !cancelled && setOpen(false));
    return () => {
      cancelled = true;
    };
  }, [open, url, fixed]);

  useEffect(() => {
    if (!waiting) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<{ name: string }[]>("connections")
        .then((all) => {
          if (all.some((c) => c.name === waiting)) {
            setWaiting(null);
            onAdded(waiting);
          } else if (Date.now() > deadline) setWaiting(null);
        })
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [waiting, onAdded]);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const { url: page } = await api<{ url: string }>("connections/signin", { json: body });
      window.open(page, "_blank", "noopener");
      setWaiting(name);
    } catch (err) {
      setError(errorText(err));
      setPasting(true);
    } finally {
      setBusy(false);
    }
  }

  async function connect() {
    setBusy(true);
    setError(null);
    try {
      // A header the server declares with a {placeholder} gets the value filled into it.
      const filled = Object.fromEntries(
        headers.map((h) => [h.name, !h.value ? (values[h.name] ?? "") : h.value.replace(/\{[^}]+\}/, values[h.name] ?? "")]),
      );
      await api("connections/remote", { json: open && !pasting ? { ...body, open: true } : named.length ? { ...body, headers: filled } : { ...body, token } });
      onAdded(name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted">{url}</p>
      <SharedChoice
        fixed={fixed}
        kind={kind}
        onKind={setKind}
        all={all}
        onAll={setAll}
        project={project}
      />
      <ConnectionBasics title={shown} setTitle={setTyped} env={env} setEnv={setEnv} shared={shared} />
      {waiting ? <Alert tone="good">Finish signing in on the page that opened in your browser. This updates when it&apos;s done.</Alert> : null}
      {open && !pasting ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">This server answers without a sign-in or a key.</p>
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!shown.trim() || !data} onClick={connect}>
              Connect
            </Button>
            <Button kind="ghost" onClick={() => setPasting(true)}>
              Use a key instead
            </Button>
          </div>
        </div>
      ) : !pasting ? (
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy} disabled={!shown.trim() || !data} onClick={signIn}>
            Sign in
          </Button>
          <Button kind="ghost" onClick={() => setPasting(true)}>
            Use a key instead
          </Button>
        </div>
      ) : (
        <div className="space-y-2">
          {named.length ? (
            named.map((h) => <Field key={h.name} label={h.name} type={h.secret ? "password" : "text"} value={values[h.name] ?? ""} onChange={(v) => setValues({ ...values, [h.name]: v })} hint={h.description || undefined} />)
          ) : (
            <Field label="Access token or API key" type="password" value={token} onChange={setToken} />
          )}
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!shown.trim() || !data || (named.length ? named.some((h) => h.required && !values[h.name]?.trim()) : !token.trim())} onClick={connect}>
              Connect
            </Button>
            {signin !== false ? (
              <Button kind="ghost" onClick={() => setPasting(false)}>
                Sign in instead
              </Button>
            ) : null}
          </div>
        </div>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** One confirmation screen: who published it, exactly what installs and runs. */
function LocalInstall({ server, index, client, project, kind: fixed, onAdded, replace }: { server: CatalogServer; index: number; replace?: string } & Target) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [kind, setKind] = useState<Kind | null>(null); // asked once the plan shows what it needs
  const [all, setAll] = useState(false);
  const [typed, setTyped] = useState<string | null>(null); // null: the suggested title
  const [env, setEnv] = useState(replace ? "dev" : ""); // an update keeps its own
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "installing">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Plan>("catalog/plan", { json: { server: server.name, package: index } })
      .then((found) => {
        setPlan(found);
        // Nothing secret to give it: most likely your own tool, not a client's account.
        const suggested: Kind = fixed ?? ([...found.env, ...found.arguments].some((f) => f.secret) ? "client" : "shared");
        setKind(suggested);
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, [server.name, server.title, index, replace, fixed, client]);

  const taken = useTakenNames();
  const shared = kind === "shared";
  const shown = typed ?? titleFor(kind ?? "client", client, server.title);
  const name = replace ?? nameFor(shown, shared ? undefined : client, taken);
  const data = shared ? "dev" : env; // your own tool holds no client's data

  async function install() {
    if (!plan) return;
    setState("installing");
    setError(null);
    try {
      await api("connections/local", { json: { server: server.name, package: index, fingerprint: plan.fingerprint, name, title: replace ? null : shown.trim(), owner: client, env: data, project: replace ? null : (project ?? null), values, replace: replace ?? null, shared, all_projects: shared && all } });
      await installed(name);
      onAdded(name);
    } catch (err) {
      setError(errorText(err));
      setState("idle");
    }
  }

  if (!plan) return error ? <Alert>{error}</Alert> : <Spinner />;
  const fields = [...plan.env, ...plan.arguments.filter((a) => !a.value)];
  const missing = fields.some((f) => f.required && !f.default && !values[f.name]?.trim());
  return (
    <div className="space-y-3 text-sm">
      <p className="font-medium">{replace ? `Update ${replace} to ${plan.version}` : "Install and run on this computer"}</p>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-muted">Publisher</dt>
        <dd>
          <Verified server={plan} />
        </dd>
        <dt className="text-muted">Package</dt>
        <dd>
          {plan.registry} <code>{plan.identifier}</code> version <strong>{plan.version}</strong>, pinned: it never updates without asking you
        </dd>
        <dt className="text-muted">Installs with</dt>
        <dd>
          <code>{plan.install}</code>, into Ringkeeper&apos;s data folder
        </dd>
        <dt className="text-muted">Runs</dt>
        <dd>
          <code>{plan.runs}</code>, started for each call with only the settings below
        </dd>
      </dl>
      {plan.missing ? <Alert tone="warn">This needs {plan.needs}, which isn&apos;t on this computer. Install it, then come back.</Alert> : null}
      {!replace && kind ? (
        <SharedChoice
          fixed={fixed}
          kind={kind}
          onKind={setKind}
          all={all}
          onAll={setAll}
          project={project}
        />
      ) : null}
      {!replace ? <ConnectionBasics title={shown} setTitle={setTyped} env={env} setEnv={setEnv} shared={shared} /> : null}
      {fields.map((f) => (
        <Field key={f.name} label={`${f.name}${f.required ? "" : " (optional)"}`} type={f.secret ? "password" : "text"} value={values[f.name] ?? ""} onChange={(v) => setValues({ ...values, [f.name]: v })} placeholder={f.default || undefined} hint={f.description || undefined} />
      ))}
      <Button kind="primary" busy={state === "installing"} disabled={plan.missing || missing || !shown.trim() || !data} onClick={install}>
        {replace ? `Update to ${plan.version}` : `Install ${plan.identifier} ${plan.version} and connect`}
      </Button>
      {state === "installing" ? <p className="text-xs text-muted">Installing… this can take a minute.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/**
 * A desktop extension (.mcpb) from its file: what its manifest says, one
 * confirmation, then it's unpacked into the data folder like a local server.
 * Same rules as an extension imported from Claude Desktop: Test data, and
 * anything not marked read-only waits for approval.
 */
function InstallExtension({ client, project, kind: fixed, onAdded }: Target) {
  const [shown, setShown] = useState<Extension | null>(null);
  const [kind, setKind] = useState<Kind>("client");
  const [all, setAll] = useState(false);
  const [typed, setTyped] = useState<string | null>(null); // null: the suggested title
  const [env, setEnv] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "reading" | "installing">("idle");
  const [error, setError] = useState<string | null>(null);
  const taken = useTakenNames();
  const shared = kind === "shared";
  const title = typed ?? (shown ? titleFor(kind, client, shown.title) : "");
  const name = nameFor(title, shared ? undefined : client, taken);

  async function open(file: File | undefined) {
    if (!file) return;
    setShown(null);
    setValues({});
    setError(null);
    setState("reading");
    try {
      const found = await api<Extension>("catalog/extensions", { body: file, contentType: "application/octet-stream" });
      setShown(found);
      const suggested: Kind = fixed ?? (found.settings.some((s) => s.secret) ? "client" : "shared");
      setKind(suggested);
      setTyped(null);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setState("idle");
    }
  }

  async function install() {
    if (!shown) return;
    setState("installing");
    setError(null);
    try {
      await api("connections/extension", { json: { fingerprint: shown.fingerprint, name, title: title.trim(), env: shared ? "dev" : env, owner: client, project: project ?? null, values, shared, all_projects: shared && all } });
      await installed(name);
      onAdded(name);
    } catch (err) {
      setError(errorText(err));
      setState("idle");
    }
  }

  const missing = shown?.settings.some((s) => s.required && !s.default && !values[s.key]?.trim()) ?? true;
  return (
    <div className="space-y-3 text-sm">
      <p className="text-xs text-muted">An extension file for Claude Desktop (.mcpb), from its publisher. Ringkeeper shows what&apos;s in it before anything is installed.</p>
      <input type="file" accept=".mcpb,.dxt" aria-label="Extension file" disabled={state !== "idle"} onChange={(event) => void open(event.target.files?.[0])} className="block w-full text-sm" />
      {state === "reading" ? <Spinner /> : null}
      {shown ? (
        <>
          <p className="font-medium">
            {shown.title} {shown.version}
          </p>
          {shown.description ? <p className="text-muted">{shown.description}</p> : null}
          <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-muted">Publisher</dt>
            <dd>
              {shown.publisher} <span className="text-xs text-muted">(as the extension says; not verified)</span>
            </dd>
            <dt className="text-muted">Tools</dt>
            <dd>
              {shown.tools.length
                ? shown.tools.map((t, i) => (
                    <span key={t.name} title={t.description || undefined}>
                      {i ? ", " : ""}
                      <code>{t.name}</code>
                    </span>
                  ))
                : "None listed"}
              {shown.tools_generated ? (shown.tools.length ? ", and others it lists when it starts" : ": it lists them when it starts") : null}
            </dd>
            <dt className="text-muted">Runs</dt>
            <dd>
              <code>{shown.runs}</code>, unpacked into Ringkeeper&apos;s data folder and started for each call with only the settings below
            </dd>
            <dt className="text-muted">File</dt>
            <dd>
              {(shown.size / 1_000_000).toFixed(1)} MB, {shown.files} files
            </dd>
          </dl>
          {shown.problem ? <Alert tone="warn">This can&apos;t run here: {shown.problem}.</Alert> : null}
          <SharedChoice
            fixed={fixed}
            kind={kind}
            onKind={setKind}
            all={all}
            onAll={setAll}
            project={project}
          />
          <ConnectionBasics title={title} setTitle={setTyped} env={env} setEnv={setEnv} shared={shared} />
          {shown.settings.map((s) =>
            s.multiple ? (
              <TextArea key={s.key} label={`${s.title}${s.required ? "" : " (optional)"}`} value={values[s.key] ?? ""} onChange={(v) => setValues({ ...values, [s.key]: v })} hint={[s.description, "One per line.", s.default ? `Default: ${s.default}` : ""].filter(Boolean).join(" ")} />
            ) : (
              <Field key={s.key} label={`${s.title}${s.required ? "" : " (optional)"}`} type={s.secret ? "password" : "text"} value={values[s.key] ?? ""} onChange={(v) => setValues({ ...values, [s.key]: v })} placeholder={s.default || undefined} hint={s.description || undefined} />
            ),
          )}
          <Button kind="primary" busy={state === "installing"} disabled={Boolean(shown.problem) || missing || !title.trim() || !(shared || env)} onClick={install}>
            Install {shown.title} {shown.version} and connect
          </Button>
          {state === "installing" ? <p className="text-xs text-muted">Installing…</p> : null}
        </>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** A client's account, or your own tool for any client (unless that was asked up front). */
function SharedChoice({ fixed, kind, onKind, all, onAll, project }: { fixed?: Kind; kind: Kind; onKind: (kind: Kind) => void; all: boolean; onAll: (on: boolean) => void; project?: string }) {
  if (fixed === "client") return null;
  return (
    <div className="space-y-2">
      {fixed ? null : <KindChoice value={kind} onChange={(next) => onKind(next === "client" ? "client" : "shared")} />}
      {kind === "shared" ? (
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={all} onChange={(e) => onAll(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
          <span>
            For <strong>all projects</strong>, new ones too
            <span className="text-muted">{all ? "" : project ? ` (otherwise only ${projectName(project)}; link it to more later)` : " (otherwise link it to projects later)"}</span>
          </span>
        </label>
      ) : null}
    </div>
  );
}

/** Any remote MCP server, by its address: sign in if it offers that, else a key. A known
 * service's address opens that service's own form here, or its built-in one: never "connect
 * it from above" with nothing above to connect. */
function AddByUrl({ services, onOpen, ...target }: Target & { services: RemoteService[]; onOpen: (id: string) => void }) {
  const [url, setUrl] = useState("");
  const [probed, setProbed] = useState<{ url: string; signin: boolean; open: boolean; recommended: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function look() {
    setBusy(true);
    setError(null);
    setProbed(null);
    try {
      setProbed(await api("catalog/probe", { json: { url: url.trim() } }));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Field label="The server's address" value={url} onChange={setUrl} placeholder="https://mcp.example.com/mcp" hint="For a tool that isn't in the list above, by its web address." />
      <Button busy={busy} disabled={!url.trim()} onClick={look}>
        Look it up
      </Button>
      {error ? <Alert>{error}</Alert> : null}
      {probed?.recommended ? (
        <KnownService name={probed.recommended} services={services} onOpen={onOpen} {...target} />
      ) : null}
      {probed && !probed.recommended ? <RemoteConnect key={probed.url} url={probed.url} headers={[]} signin={probed.signin} open={probed.open} title={new URL(probed.url).hostname.split(".").slice(-2, -1)[0] ?? "server"} {...target} /> : null}
    </div>
  );
}

/** A typed address that's a service Ringkeeper knows: its own server's form, right here, or
 * (when its server isn't offered, like GitHub's) its built-in connection. */
function KnownService({ name, services, onOpen, ...target }: Target & { name: string; services: RemoteService[]; onOpen: (id: string) => void }) {
  const service = services.find((s) => s.key === name);
  const tile = BUILT_IN.find((t) => t.key === name);
  if (service) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted">That&apos;s {service.name}&apos;s own server, with Ringkeeper&apos;s rules for it.</p>
        <ConnectRemote service={service} client={target.client} project={target.project} shared={target.kind === "shared"} onAdded={target.onAdded} />
      </div>
    );
  }
  if (tile) {
    return (
      <div className="space-y-2">
        <p className="text-sm text-muted">Ringkeeper connects {tile.label} with its built-in connection: {tile.sub.toLowerCase()}.</p>
        <Button kind="primary" onClick={() => onOpen(`tile:${tile.key}`)}>
          Connect {tile.label}
        </Button>
      </div>
    );
  }
  return <Alert>Ringkeeper knows that address, but can&apos;t connect it here yet.</Alert>;
}

/** A local server with a newer version in the catalog: offered, never installed by itself. */
export function LocalUpdate({ connection, client, onDone }: { connection: string; client: string; onDone: () => void }) {
  const updates = useApi<{ connection: string; server: string; installed: string; latest: string }[]>("catalog/updates");
  const found = updates.data?.find((u) => u.connection === connection);
  const [server, setServer] = useState<CatalogServer | null>(null);
  const [failed, setFailed] = useState(false);
  if (!found) return null;
  if (!server) {
    return (
      <Alert tone="warn">
        Version {found.latest} is available (installed: {found.installed}).{" "}
        <button type="button" className="underline" onClick={() => api<CatalogServer>(`catalog/servers/${found.server}`).then(setServer, () => setFailed(true))}>
          Review the update
        </button>
        {failed ? " Couldn't load it; check this computer is online and try again." : null}
      </Alert>
    );
  }
  const index = server.packages.findIndex((p) => p.usable);
  return <LocalInstall server={server} index={index} client={client} replace={connection} onAdded={() => { setServer(null); updates.reload(); onDone(); }} />;
}
