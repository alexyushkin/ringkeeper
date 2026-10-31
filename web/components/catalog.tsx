"use client";

import { useEffect, useState } from "react";

import { RemoteServices } from "@/components/connection-library";
import { KindChoice, type Kind } from "@/components/shared-tools";
import { Alert, Badge, Button, Details, Field, Select, Spinner, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { ENVS, slugify } from "@/lib/client/words";

/**
 * The open catalog: the Recommended shelf (services with built-in rules: scope
 * narrowing, SQL checks), then any MCP server from the MCP Registry (searchable,
 * and from a cache when offline), then any remote server by its address, then a
 * desktop extension from its file. Every connection belongs to `client`, or is a
 * shared tool (yours, for any client's projects); with `project`, it's linked to
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

/** A client's connection is named after the client; your own tool after itself. */
const nameFor = (kind: Kind, client: string, title: string) => slugify(kind === "shared" ? title : `${client} ${title}`).slice(0, 60);

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
    if (job.state === "failed") throw new Error(job.error ?? "The install failed.");
    if (Date.now() > deadline) throw new Error("The install is taking too long. Check back later.");
  }
}

export function Verified({ server }: { server: { publisher: string; verified: string } }) {
  return (
    <span className="text-xs text-muted" title={server.verified === "domain" ? "The registry checked that the publisher controls this domain." : "The registry checked that the publisher signed in as this GitHub account."}>
      by {server.publisher} ✓ {server.verified === "domain" ? "domain verified" : "GitHub account"}
    </span>
  );
}

function SignInBadge({ signin }: { signin: boolean | null }) {
  if (signin === true) return <Badge tone="good">one-click sign-in</Badge>;
  if (signin === false) return <Badge>needs a key</Badge>;
  return null;
}

export function Catalog(target: Target) {
  return (
    <div className="space-y-4">
      {target.kind === "shared" ? (
        <p className="text-sm text-muted">
          Yours, not a client&apos;s: a browser, files on this computer, a public docs server, or your own account with a service. Any client&apos;s projects can use it, or all of them.
        </p>
      ) : null}
      <div className="space-y-2">
        <h3 className="font-medium">Recommended</h3>
        <p className="text-xs text-muted">These have built-in rules: a project&apos;s link can be narrowed (to one Neon project, one Airtable base and so on), and SQL is checked before it runs.</p>
        <RemoteServices client={target.client} project={target.project} shared={target.kind === "shared"} onAdded={target.onAdded} />
      </div>
      <CatalogSearch {...target} />
      <Details label="Add a server by its address">
        <AddByUrl {...target} />
      </Details>
      <Details label="Install extension (.mcpb file)">
        <InstallExtension {...target} />
      </Details>
    </div>
  );
}

function CatalogSearch(target: Target) {
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

  const servers = pages.flatMap((p) => p.servers);
  const last = pages[pages.length - 1];
  return (
    <div className="space-y-2">
      <h3 className="font-medium">All MCP servers</h3>
      <Field label="Search the MCP Registry" value={typed} onChange={setTyped} placeholder="notion, postgres, weather…" />
      {last?.offline ? <Alert tone="warn">Offline: showing servers seen before{last.cached_at ? ` (as of ${new Date(last.cached_at * 1000).toLocaleDateString()})` : ""}.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      <ul className="space-y-2">
        {servers.map((server) => (
          <li key={server.name}>
            <button type="button" aria-expanded={chosen?.name === server.name} onClick={() => setChosen(chosen?.name === server.name ? null : server)} className={`w-full rounded-xl border px-3 py-2 text-left text-sm hover:border-accent ${chosen?.name === server.name ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}>
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{server.title}</span>
                {server.kind.includes("remote") ? <Badge>remote</Badge> : null}
                {server.kind.includes("local") ? <Badge>runs on this computer</Badge> : null}
                <SignInBadge signin={server.signin} />
                {server.recommended ? <Badge tone="good">built-in rules</Badge> : null}
                {server.status === "deprecated" ? <Badge tone="warn">deprecated</Badge> : null}
              </span>
              {chosen?.name === server.name ? (
                <>
                  <span className="mt-1 block text-xs text-muted">{server.name}</span>
                  <span className="mt-1 block text-muted">{server.description}</span>
                  <Verified server={server} />
                </>
              ) : (
                <span className="mt-0.5 block truncate text-muted">{server.description}</span>
              )}
            </button>
            {chosen?.name === server.name ? <ServerConnect key={server.name} server={server} {...target} /> : null}
          </li>
        ))}
        {!busy && !servers.length && !error ? <li className="text-sm text-muted">Nothing found.</li> : null}
      </ul>
      {busy ? <Spinner /> : null}
      {last?.next_cursor && !busy ? (
        <Button kind="ghost" onClick={more}>
          More
        </Button>
      ) : null}
    </div>
  );
}

function ConnectionBasics({ name, setName, env, setEnv }: { name: string; setName: (v: string) => void; env: string; setEnv: (v: string) => void }) {
  return (
    <>
      <Field label="Name" value={name} onChange={(v) => setName(slugify(v))} hint="A short name the agent uses for it." />
      <Select label="Kind of data" value={env} onChange={setEnv} options={ENVS} hint={env === "prod" ? "Changes to production always wait for your approval." : "Test data: tools marked read-only run without asking; everything else waits for you."} />
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
  const [name, setName] = useState(nameFor(kind, client, title));
  const [env, setEnv] = useState("dev");
  const [token, setToken] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [pasting, setPasting] = useState(signin === false && !open);
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const shared = kind === "shared";
  const body = { url, server: server ?? null, name, owner: client, env, project: project ?? null, shared, all_projects: shared && all };

  useEffect(() => {
    // From the catalog: ask the server whether it needs anything at all.
    if (open !== null) return;
    let cancelled = false;
    api<{ open: boolean }>("catalog/probe", { json: { url } })
      .then((found) => {
        if (cancelled) return;
        setOpen(found.open);
        if (found.open) setPasting(false);
        if (found.open && !fixed) {
          setKind("shared");
          setName(nameFor("shared", client, title));
        }
      })
      .catch(() => !cancelled && setOpen(false));
    return () => {
      cancelled = true;
    };
  }, [open, url, fixed, client, title]);

  useEffect(() => {
    if (!waiting) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<{ name: string }[]>("connections")
        .then((all) => {
          if (all.some((c) => c.name === name)) {
            setWaiting(false);
            onAdded(name);
          } else if (Date.now() > deadline) setWaiting(false);
        })
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [waiting, name, onAdded]);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const { url: page } = await api<{ url: string }>("connections/signin", { json: body });
      window.open(page, "_blank", "noopener");
      setWaiting(true);
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
        onKind={(next) => {
          setKind(next);
          setName(nameFor(next, client, title));
        }}
        all={all}
        onAll={setAll}
        project={project}
      />
      <ConnectionBasics name={name} setName={setName} env={env} setEnv={setEnv} />
      {waiting ? <Alert tone="good">Finish signing in on the page that opened in your browser. This updates when it&apos;s done.</Alert> : null}
      {open && !pasting ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">This server answers without a sign-in or a key.</p>
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!name} onClick={connect}>
              Connect
            </Button>
            <Button kind="ghost" onClick={() => setPasting(true)}>
              Use a key instead
            </Button>
          </div>
        </div>
      ) : !pasting ? (
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy} disabled={!name} onClick={signIn}>
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
            <Field label="Token (sent as Authorization: Bearer)" type="password" value={token} onChange={setToken} />
          )}
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!name || (named.length ? named.some((h) => h.required && !values[h.name]?.trim()) : !token.trim())} onClick={connect}>
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
  const [name, setName] = useState(replace ?? nameFor(fixed ?? "client", client, server.title));
  const [env, setEnv] = useState("dev");
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
        if (!replace) setName(nameFor(suggested, client, server.title));
      })
      .catch((err: unknown) => setError(errorText(err)));
  }, [server.name, server.title, index, replace, fixed, client]);

  async function install() {
    if (!plan) return;
    setState("installing");
    setError(null);
    try {
      const shared = kind === "shared";
      await api("connections/local", { json: { server: server.name, package: index, fingerprint: plan.fingerprint, name, owner: client, env, project: replace ? null : (project ?? null), values, replace: replace ?? null, shared, all_projects: shared && all } });
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
          onKind={(next) => {
            setKind(next);
            setName(nameFor(next, client, server.title));
          }}
          all={all}
          onAll={setAll}
          project={project}
        />
      ) : null}
      {!replace ? <ConnectionBasics name={name} setName={setName} env={env} setEnv={setEnv} /> : null}
      {fields.map((f) => (
        <Field key={f.name} label={`${f.name}${f.required ? "" : " (optional)"}`} type={f.secret ? "password" : "text"} value={values[f.name] ?? ""} onChange={(v) => setValues({ ...values, [f.name]: v })} placeholder={f.default || undefined} hint={f.description || undefined} />
      ))}
      <Button kind="primary" busy={state === "installing"} disabled={plan.missing || missing || !name} onClick={install}>
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
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "reading" | "installing">("idle");
  const [error, setError] = useState<string | null>(null);

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
      setName(nameFor(suggested, client, found.title));
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
      const shared = kind === "shared";
      await api("connections/extension", { json: { fingerprint: shown.fingerprint, name, owner: client, project: project ?? null, values, shared, all_projects: shared && all } });
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
            onKind={(next) => {
              setKind(next);
              setName(nameFor(next, client, shown.title));
            }}
            all={all}
            onAll={setAll}
            project={project}
          />
          <Field label="Name" value={name} onChange={(v) => setName(slugify(v))} hint="A short name the agent uses for it." />
          <p className="text-xs text-muted">Added as Test data, like an extension imported from Claude Desktop: tools marked read-only run without asking; everything else waits for you.</p>
          {shown.settings.map((s) =>
            s.multiple ? (
              <TextArea key={s.key} label={`${s.title}${s.required ? "" : " (optional)"}`} value={values[s.key] ?? ""} onChange={(v) => setValues({ ...values, [s.key]: v })} hint={[s.description, "One per line.", s.default ? `Default: ${s.default}` : ""].filter(Boolean).join(" ")} />
            ) : (
              <Field key={s.key} label={`${s.title}${s.required ? "" : " (optional)"}`} type={s.secret ? "password" : "text"} value={values[s.key] ?? ""} onChange={(v) => setValues({ ...values, [s.key]: v })} placeholder={s.default || undefined} hint={s.description || undefined} />
            ),
          )}
          <Button kind="primary" busy={state === "installing"} disabled={Boolean(shown.problem) || missing || !name} onClick={install}>
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
            <span className="text-muted">{all ? "" : project ? ` (otherwise only ${project}; link it to more later)` : " (otherwise link it to projects later)"}</span>
          </span>
        </label>
      ) : null}
    </div>
  );
}

/** Any remote MCP server, by its address: sign in if it offers that, else a key. */
function AddByUrl(target: Target) {
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
      <Field label="The server's address" value={url} onChange={setUrl} placeholder="https://mcp.example.com/mcp" hint="For a remote MCP server that isn't in the registry." />
      <Button busy={busy} disabled={!url.trim()} onClick={look}>
        Look it up
      </Button>
      {error ? <Alert>{error}</Alert> : null}
      {probed?.recommended ? (
        <Alert tone="good">That&apos;s a service with built-in rules: connect it from Recommended above.</Alert>
      ) : null}
      {probed && !probed.recommended ? <RemoteConnect key={probed.url} url={probed.url} headers={[]} signin={probed.signin} open={probed.open} title={new URL(probed.url).hostname.split(".").slice(-2, -1)[0] ?? "server"} {...target} /> : null}
    </div>
  );
}

/** A local server with a newer version in the catalog: offered, never installed by itself. */
export function LocalUpdate({ connection, client, onDone }: { connection: string; client: string; onDone: () => void }) {
  const updates = useApi<{ connection: string; server: string; installed: string; latest: string }[]>("catalog/updates");
  const found = updates.data?.find((u) => u.connection === connection);
  const [server, setServer] = useState<CatalogServer | null>(null);
  if (!found) return null;
  if (!server) {
    return (
      <Alert tone="warn">
        Version {found.latest} is available (installed: {found.installed}).{" "}
        <button type="button" className="underline" onClick={() => api<CatalogServer>(`catalog/servers/${found.server}`).then(setServer).catch(() => undefined)}>
          Review the update
        </button>
      </Alert>
    );
  }
  const index = server.packages.findIndex((p) => p.usable);
  return <LocalInstall server={server} index={index} client={client} replace={connection} onAdded={() => { setServer(null); updates.reload(); onDone(); }} />;
}
