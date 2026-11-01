"use client";

import Link from "@/components/link";
import { useEffect, useState } from "react";

import { LocalUpdate } from "@/components/catalog";
import { TestButton, type ConnectionData } from "@/components/forms";
import { GoogleAccount } from "@/components/google";
import { McpTools } from "@/components/mcp-tools";
import { Alert, Badge, Button, CompactRow, ConfirmButton, Details, EnvBadge, Field, Select, Toggle, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { projectHref } from "@/lib/client/routes";
import { ENVS, SERVICE, slugify } from "@/lib/client/words";

/** A connection library: one client's connections, each linked to some of their
 * projects (optionally narrowed), plus the services they can sign in to. */

export interface ScopeField {
  key: string;
  label: string;
  hint: string;
}

export interface RemoteService {
  key: string;
  name: string;
  signin: boolean;
  paste: string | null;
  paste_url: string;
  paste_steps: string;
  note: string;
  scope: ScopeField[];
}

export interface LinkInfo {
  project: string;
  scope: Record<string, string>;
  is_default: boolean;
}

export type LibraryConnection = ConnectionData & { links?: LinkInfo[]; scope_fields?: ScopeField[] };

export function serviceName(connection: LibraryConnection, services: RemoteService[] = []): string {
  const remote = typeof connection.config.remote === "string" ? connection.config.remote : null;
  if (remote) return services.find((s) => s.key === remote)?.name ?? remote;
  if (typeof connection.config.registry === "string") return connection.config.registry;
  return SERVICE[connection.type] ?? connection.type;
}

export function scopeText(scope: Record<string, string>, fields: ScopeField[] = []): string {
  const parts = Object.entries(scope).map(([key, value]) => `${fields.find((f) => f.key === key)?.label ?? key} ${value}`);
  return parts.length ? `limited to ${parts.join(", ")}` : "the whole connection";
}

export function ScopeInputs({ fields, value, onChange }: { fields: ScopeField[]; value: Record<string, string>; onChange: (next: Record<string, string>) => void }) {
  if (!fields.length) return null;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">Narrow what this project can reach. Leave empty for all of it.</p>
      {fields.map((field) => (
        <Field key={field.key} label={field.label} value={value[field.key] ?? ""} onChange={(v) => onChange({ ...value, [field.key]: v.trim() })} hint={field.hint} />
      ))}
    </div>
  );
}

function cleaned(scope: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(scope).filter(([, v]) => v.trim()));
}

/** One link: the project, what it's narrowed to, and changing or removing it. */
function LinkRow({ connection, link, onChange }: { connection: LibraryConnection; link: LinkInfo; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [scope, setScope] = useState(link.scope);
  const [error, setError] = useState<string | null>(null);
  const fields = connection.scope_fields ?? [];

  async function save(next: Record<string, string> | null) {
    setError(null);
    try {
      if (next === null) await api(`projects/${link.project}/connections/${connection.name}`, { method: "DELETE" });
      else await api(`projects/${link.project}/connections/${connection.name}`, { method: "PUT", json: { is_default: link.is_default, scope: cleaned(next) } });
      setEditing(false);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <li className="space-y-2 rounded-xl bg-sunken p-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={projectHref(link.project)} className="font-medium text-accent">
          {link.project}
        </Link>
        <span className="text-muted">{scopeText(link.scope, fields)}</span>
        {link.is_default ? <Badge>default</Badge> : null}
        <span className="ml-auto" />
        {fields.length ? (
          <Button kind="ghost" onClick={() => setEditing(!editing)}>
            {editing ? "Cancel" : "Narrow"}
          </Button>
        ) : null}
        <Button kind="ghost" onClick={() => save(null)}>
          Unlink
        </Button>
      </div>
      {editing ? (
        <div className="space-y-2">
          <ScopeInputs fields={fields} value={scope} onChange={setScope} />
          <Button kind="primary" onClick={() => save(scope)}>
            Save
          </Button>
        </div>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </li>
  );
}

/** Link a client's connection to another of their projects, narrowed or not. */
function LinkToProject({ connection, projects, onChange }: { connection: LibraryConnection; projects: string[]; onChange: () => void }) {
  const linked = new Set((connection.links ?? []).map((l) => l.project));
  const open = projects.filter((p) => !linked.has(p));
  const [project, setProject] = useState("");
  const [scope, setScope] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  if (!open.length) return null;

  async function link() {
    setError(null);
    try {
      await api(`projects/${project}/connections/${connection.name}`, { method: "PUT", json: { is_default: false, scope: cleaned(scope) } });
      setProject("");
      setScope({});
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Details label="Link to another project">
      <div className="space-y-2">
        <Select label="Project" value={project} onChange={setProject} options={[["", "Choose…"], ...open.map((p): [string, string] => [p, p])]} />
        {project ? <ScopeInputs fields={connection.scope_fields ?? []} value={scope} onChange={setScope} /> : null}
        <Button kind="primary" disabled={!project} onClick={link}>
          Link
        </Button>
        {error ? <Alert>{error}</Alert> : null}
      </div>
    </Details>
  );
}

/** A shared tool for every project, new ones too (or only the ones it's linked to),
 * maybe except some clients (their projects, new ones too) and some projects. */
function AllProjects({ connection, onChange }: { connection: LibraryConnection; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const all = Boolean(connection.all_projects);
  const clients = connection.except_clients ?? [];
  const projects = connection.except_projects ?? [];
  async function patch(json: Record<string, unknown>) {
    setError(null);
    try {
      await api(`connections/${connection.name}`, { method: "PATCH", json });
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <>
      <Toggle
        label="All projects"
        checked={all}
        onChange={(on) => patch({ all_projects: on })}
        hint={
          !all
            ? "Only the projects below. Turn on to give it to every project, new ones too."
            : clients.length || projects.length
              ? `${connection.reach}. New projects get it too, unless their client is left out.`
              : "Every project has it, and new projects get it too."
        }
      />
      {all ? <Exceptions clients={clients} projects={projects} onSave={patch} /> : null}
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
}

/** "All projects except…": clients (their projects, new ones too) and projects. */
function Exceptions({ clients, projects, onSave }: { clients: string[]; projects: string[]; onSave: (json: Record<string, unknown>) => void }) {
  const known = useApi<{ name: string; owner: string }[]>("projects");
  const [pick, setPick] = useState("");
  const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();
  const allClients = [...new Map((known.data ?? []).map((p) => [p.owner.trim().toLowerCase(), p.owner])).values()].sort((a, b) => a.localeCompare(b));
  const clientChoices = allClients.filter((c) => !clients.some((x) => same(x, c)));
  // A project of a client already left out is left out anyway.
  const projectChoices = (known.data ?? []).filter((p) => !projects.includes(p.name) && !clients.some((c) => same(c, p.owner))).map((p) => p.name);
  function add(value: string) {
    setPick("");
    if (value.startsWith("client:")) onSave({ except_clients: [...clients, value.slice(7)] });
    else if (value.startsWith("project:")) onSave({ except_projects: [...projects, value.slice(8)] });
  }
  const left = [...clients.map((c) => ({ key: `client:${c}`, label: `${c} (client)` })), ...projects.map((p) => ({ key: `project:${p}`, label: p }))];
  return (
    <div className="space-y-2">
      {left.length ? (
        <ul className="flex flex-wrap gap-2" aria-label="Left out">
          {left.map((item) => (
            <li key={item.key} className="flex items-center gap-1 rounded-full border border-line px-3 py-1 text-sm">
              {item.label}
              <button
                type="button"
                className="ml-1 text-muted hover:text-ink"
                aria-label={`Give it back to ${item.label}`}
                onClick={() =>
                  item.key.startsWith("client:")
                    ? onSave({ except_clients: clients.filter((c) => `client:${c}` !== item.key) })
                    : onSave({ except_projects: projects.filter((p) => `project:${p}` !== item.key) })
                }
              >
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {clientChoices.length || projectChoices.length ? (
        <Select
          label="Except…"
          value={pick}
          onChange={add}
          options={[
            ["", left.length ? "Leave out another client or project…" : "Leave out a client or a project…"],
            ...clientChoices.map((c): [string, string] => [`client:${c}`, `${c} (client: all their projects, new ones too)`]),
            ...projectChoices.map((p): [string, string] => [`project:${p}`, p]),
          ]}
        />
      ) : null}
    </div>
  );
}

/** A connection in its client's library (or a shared tool, in the shared tools). */
export function LibraryCard({ connection, projects, services, onChange, compact = false }: { connection: LibraryConnection; projects: string[]; services: RemoteService[]; onChange: () => void; compact?: boolean }) {
  // A link to this connection (connectionHref) opens it and scrolls to it.
  const [targeted] = useState(() => typeof window !== "undefined" && window.location.hash === `#connections/${encodeURIComponent(connection.name)}`);
  useEffect(() => {
    if (targeted) document.getElementById(`connection-${connection.name}`)?.scrollIntoView({ block: "center" });
  }, [targeted, connection.name]);
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const remote = typeof connection.config.remote === "string";
  const fromCatalog = typeof connection.config.registry === "string";
  const imported = connection.type === "mcp" && !remote && !fromCatalog;

  async function replace() {
    setError(null);
    try {
      await api(`connections/${connection.name}/secret`, { method: "PUT", json: { secret } });
      setSecret("");
      setSaved(true);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    try {
      for (const link of connection.links ?? []) await api(`projects/${link.project}/connections/${connection.name}`, { method: "DELETE" });
      await api(`connections/${connection.name}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const links = connection.links ?? [];
  const head = (
    <>
      <span className="font-medium">{connection.name}</span>
      {/* A Google account's writes all ask first, whatever the data: no kind-of-data badge. */}
      {connection.type === "google" ? null : <EnvBadge env={connection.env} />}
      <span className="text-xs text-muted">{serviceName(connection, services)}</span>
      {connection.shared ? <Badge tone="accent">shared tool</Badge> : null}
      {connection.config.source === "signin" ? <Badge tone="good">signed in</Badge> : null}
      {connection.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
      {connection.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
      {compact ? (
        <span className="ml-auto text-xs text-muted">{connection.all_projects ? (connection.reach || "All projects") : links.length === 1 ? "1 project" : `${links.length} projects`}</span>
      ) : connection.last_test_at ? (
        <span className="ml-auto text-xs text-muted">tested {timeAgo(connection.last_test_at)}</span>
      ) : null}
    </>
  );
  const body = (
    <>
      {connection.last_test_ok === false && connection.last_test_message ? <Alert>{connection.last_test_message}</Alert> : null}
      {connection.shared ? <AllProjects connection={connection} onChange={onChange} /> : null}
      <ul className="space-y-1">
        {(connection.links ?? []).map((link) => (
          <LinkRow key={link.project} connection={connection} link={link} onChange={onChange} />
        ))}
        {!(connection.links ?? []).length ? <li className="text-sm text-muted">Not linked to a project yet.</li> : null}
      </ul>
      {/* For all projects, the only ones it isn't linked to are the ones left out. */}
      {connection.all_projects ? null : <LinkToProject connection={connection} projects={projects} onChange={onChange} />}
      {connection.config.package ? <LocalUpdate connection={connection.name} client={connection.owner} onDone={onChange} /> : null}
      <TestButton name={connection.name} onDone={() => onChange()} />
      {connection.type === "mcp" ? <McpTools name={connection.name} onChange={onChange} /> : null}
      {connection.type === "google" ? <GoogleAccount name={connection.name} onChange={onChange} /> : null}
      <Details label="Change">
        <div className="space-y-3">
          {imported ? <p className="text-sm text-muted">Its settings came from Claude Desktop. To change them, change them there, remove this connection, and import it again.</p> : null}
          {remote || fromCatalog ? <p className="text-sm text-muted">To sign in again or use another key, remove it and connect again.</p> : null}
          {connection.type !== "mcp" && connection.type !== "google" ? (
            <>
              <p className="text-sm">Secret: {connection.secret_stored ? "saved ✓ (hidden)" : "missing"}</p>
              <Field label="New secret" type="password" value={secret} onChange={setSecret} />
              <Button kind="primary" disabled={!secret} onClick={replace}>
                Replace secret
              </Button>
            </>
          ) : null}
          <ConfirmButton question={`Remove ${connection.name}? Its projects lose it too.`} confirm={`Remove ${connection.name}`} onConfirm={remove}>
            Remove connection
          </ConfirmButton>
          {saved ? <Alert tone="good">Saved. Test the connection to check it.</Alert> : null}
        </div>
      </Details>
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
  if (compact) {
    return (
      <div id={`connection-${connection.name}`}>
        <CompactRow head={head} defaultOpen={targeted}>
          {body}
        </CompactRow>
      </div>
    );
  }
  return (
    <div id={`connection-${connection.name}`} className="space-y-3 rounded-2xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-center gap-2">{head}</div>
      {body}
    </div>
  );
}

/**
 * The services a client can sign in to (one click opens the service's own page)
 * or paste a token for. With `project`, the new connection is linked to it, and
 * the link can be narrowed right away.
 */
export function RemoteServices({ client, project, shared = false, onAdded }: { client: string; project?: string; shared?: boolean; onAdded: (name: string) => void }) {
  const services = useApi<RemoteService[]>("remote-services");
  const [chosen, setChosen] = useState<RemoteService | null>(null);
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {(services.data ?? []).map((service) => (
          <button
            key={service.key}
            type="button"
            onClick={() => setChosen(chosen?.key === service.key ? null : service)}
            className={`rounded-xl border p-3 text-left text-sm hover:border-accent ${chosen?.key === service.key ? "border-accent bg-accent-soft" : "border-line bg-surface"}`}
          >
            <span className="block font-medium">{service.name}</span>
            <span className="text-xs text-muted">{service.signin ? "Sign in" : "Paste a token"}</span>
          </button>
        ))}
      </div>
      {services.error ? <Alert>{services.error}</Alert> : null}
      {chosen ? <ConnectRemote key={chosen.key} service={chosen} client={client} project={project} shared={shared} onAdded={onAdded} /> : null}
    </div>
  );
}

function ConnectRemote({ service, client, project, shared = false, onAdded }: { service: RemoteService; client: string; project?: string; shared?: boolean; onAdded: (name: string) => void }) {
  const [name, setName] = useState(slugify(shared ? service.key : `${client} ${service.key}`));
  const [env, setEnv] = useState("dev");
  const [scope, setScope] = useState<Record<string, string>>({});
  const [token, setToken] = useState("");
  const [pasting, setPasting] = useState(!service.signin);
  const [waiting, setWaiting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const body = { remote: service.key, name, owner: client, env, project: project ?? null, scope: cleaned(scope), shared };

  // After the service's page opens: the connection appears once its sign-in finishes.
  useEffect(() => {
    if (!waiting) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<ConnectionData[]>("connections")
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
      const { url } = await api<{ url: string }>("connections/signin", { json: body });
      window.open(url, "_blank", "noopener");
      setWaiting(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function paste() {
    setBusy(true);
    setError(null);
    try {
      await api("connections/remote", { json: { ...body, token } });
      setToken("");
      onAdded(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <h3 className="font-medium">Connect {service.name}</h3>
      {service.note ? <p className="text-sm text-muted">{service.note}</p> : null}
      <Field label="Name" value={name} onChange={(v) => setName(slugify(v))} hint="A short name the agent uses for it." />
      <Select label="Kind of data" value={env} onChange={setEnv} options={ENVS} hint={env === "prod" ? "Changes to production always wait for your approval." : undefined} />
      {project ? <ScopeInputs fields={service.scope} value={scope} onChange={setScope} /> : null}
      {waiting ? <Alert tone="good">Finish signing in on {service.name}&apos;s page that opened in your browser. This updates when it&apos;s done.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {service.signin && !pasting ? (
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy} disabled={!name} onClick={signIn}>
            Sign in with {service.name}
          </Button>
          {service.paste ? (
            <Button kind="ghost" onClick={() => setPasting(true)}>
              Use a token instead
            </Button>
          ) : null}
        </div>
      ) : null}
      {pasting && service.paste ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">
            {service.paste_steps}{" "}
            {service.paste_url ? (
              <a href={service.paste_url} target="_blank" rel="noreferrer" className="text-accent underline">
                Make one
              </a>
            ) : null}
          </p>
          <Field label={service.paste} type="password" value={token} onChange={setToken} />
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!name || !token.trim()} onClick={paste}>
              Connect
            </Button>
            {service.signin ? (
              <Button kind="ghost" onClick={() => setPasting(false)}>
                Sign in instead
              </Button>
            ) : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

export function useLibrary(client: string) {
  const connections = useApi<LibraryConnection[]>("connections");
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const services = useApi<RemoteService[]>("remote-services");
  const same = (owner: string) => owner.trim().toLowerCase() === client.trim().toLowerCase();
  return {
    connections: (connections.data ?? []).filter((c) => !c.shared && same(c.owner)),
    projects: (projects.data ?? []).filter((p) => same(p.owner)).map((p) => p.name),
    services: services.data ?? [],
    loading: connections.loading || projects.loading,
    error: connections.error ?? projects.error,
    reload: () => {
      connections.reload();
      projects.reload();
    },
  };
}

