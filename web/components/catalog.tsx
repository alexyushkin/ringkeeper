"use client";

import { useEffect, useState } from "react";

import { RemoteServices } from "@/components/connection-library";
import { Alert, Badge, Button, Details, Field, Select, Spinner } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { ENVS, slugify } from "@/lib/client/words";

/**
 * The open catalog: the Recommended shelf (services with built-in rules: scope
 * narrowing, SQL checks), then any MCP server from the MCP Registry (searchable,
 * and from a cache when offline), then any remote server by its address. Every
 * connection belongs to `client`; with `project`, it's linked to that project.
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

interface Target {
  client: string;
  project?: string;
  onAdded: (name: string) => void;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
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
      <div className="space-y-2">
        <h3 className="font-medium">Recommended</h3>
        <p className="text-xs text-muted">These have built-in rules: a project&apos;s link can be narrowed (to one Neon project, one Airtable base and so on), and SQL is checked before it runs.</p>
        <RemoteServices client={target.client} project={target.project} onAdded={target.onAdded} />
      </div>
      <CatalogSearch {...target} />
      <Details label="Add a server by its address">
        <AddByUrl {...target} />
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
            <button type="button" onClick={() => setChosen(chosen?.name === server.name ? null : server)} className={`w-full rounded-xl border p-3 text-left text-sm hover:border-accent ${chosen?.name === server.name ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}>
              <span className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{server.title}</span>
                <span className="text-xs text-muted">{server.name}</span>
                {server.kind.includes("remote") ? <Badge>remote</Badge> : null}
                {server.kind.includes("local") ? <Badge>runs on this computer</Badge> : null}
                <SignInBadge signin={server.signin} />
                {server.recommended ? <Badge tone="good">built-in rules</Badge> : null}
                {server.status === "deprecated" ? <Badge tone="warn">deprecated</Badge> : null}
              </span>
              <span className="mt-1 block text-muted">{server.description}</span>
              <Verified server={server} />
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
function ServerConnect({ server, client, project, onAdded }: { server: CatalogServer } & Target) {
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
        <RemoteConnect url={remote.url} server={server.name} headers={remote.headers} signin={server.signin} title={server.title} client={client} project={project} onAdded={onAdded} />
      ) : (
        <LocalInstall server={server} index={packageIndex} client={client} project={project} onAdded={onAdded} />
      )}
    </div>
  );
}

/** A remote server without built-in rules: sign in, or give it the token or headers it needs. */
function RemoteConnect({ url, server, headers, signin, title, client, project, onAdded }: { url: string; server?: string; headers: Header[]; signin: boolean | null; title: string } & Target) {
  const [name, setName] = useState(slugify(`${client} ${title}`).slice(0, 60));
  const [env, setEnv] = useState("dev");
  const [token, setToken] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [pasting, setPasting] = useState(signin === false);
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = { url, server: server ?? null, name, owner: client, env, project: project ?? null };
  // Headers to fill in: no value, or a value with a {placeholder} in it.
  const named = headers.filter((h) => !h.value || h.value.includes("{"));

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
      await api("connections/remote", { json: named.length ? { ...body, headers: filled } : { ...body, token } });
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
      <ConnectionBasics name={name} setName={setName} env={env} setEnv={setEnv} />
      {waiting ? <Alert tone="good">Finish signing in on the page that opened in your browser. This updates when it&apos;s done.</Alert> : null}
      {!pasting ? (
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
function LocalInstall({ server, index, client, project, onAdded, replace }: { server: CatalogServer; index: number; replace?: string } & Target) {
  const [plan, setPlan] = useState<Plan | null>(null);
  const [name, setName] = useState(replace ?? slugify(`${client} ${server.title}`).slice(0, 60));
  const [env, setEnv] = useState("dev");
  const [values, setValues] = useState<Record<string, string>>({});
  const [state, setState] = useState<"idle" | "installing">("idle");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Plan>("catalog/plan", { json: { server: server.name, package: index } })
      .then(setPlan)
      .catch((err: unknown) => setError(errorText(err)));
  }, [server.name, index]);

  async function install() {
    if (!plan) return;
    setState("installing");
    setError(null);
    try {
      await api("connections/local", { json: { server: server.name, package: index, fingerprint: plan.fingerprint, name, owner: client, env, project: replace ? null : (project ?? null), values, replace: replace ?? null } });
      const deadline = Date.now() + 15 * 60_000;
      for (;;) {
        await new Promise((r) => setTimeout(r, 1500));
        const job = await api<{ state: string; error: string | null }>(`connections/local/${name}`);
        if (job.state === "done") return onAdded(name);
        if (job.state === "failed") throw new Error(job.error ?? "The install failed.");
        if (Date.now() > deadline) throw new Error("The install is taking too long. Check back later.");
      }
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

/** Any remote MCP server, by its address: sign in if it offers that, else a key. */
function AddByUrl(target: Target) {
  const [url, setUrl] = useState("");
  const [probed, setProbed] = useState<{ url: string; signin: boolean; recommended: string | null } | null>(null);
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
      {probed?.recommended ? <Alert tone="good">That&apos;s a service with built-in rules: connect it from Recommended above.</Alert> : null}
      {probed && !probed.recommended ? <RemoteConnect key={probed.url} url={probed.url} headers={[]} signin={probed.signin} title={new URL(probed.url).hostname.split(".").slice(-2, -1)[0] ?? "server"} {...target} /> : null}
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
