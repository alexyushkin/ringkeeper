"use client";

import Link from "@/components/link";
import { useEffect, useState } from "react";

import { LocalUpdate, ServiceMark } from "@/components/catalog";
import { connectionTitle, defaultTitle, EnvChoice, nameFor, useTakenNames } from "@/components/connection-form";
import { TestButton, type ConnectionData } from "@/components/forms";
import { GoogleAccount } from "@/components/google";
import { McpTools } from "@/components/mcp-tools";
import { EveryProject } from "@/components/memory";
import { Alert, Badge, Button, ConfirmButton, Details, EnvBadge, Field, LinkButton, Select, Toggle, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientHref, projectHref } from "@/lib/client/routes";
import { clientName, projectName, sameClient, useScope } from "@/lib/client/scope";
import { SERVICE } from "@/lib/client/words";

/** A connection library: one client's connections, each linked to some of their
 * projects (optionally narrowed), plus the services they can sign in to. */

export interface ScopeField {
  key: string;
  label: string;
  hint: string;
  many?: boolean; // several values, comma-separated (GitHub's repositories)
}

/** What a connection is, for the warning below: its kind, and whether it's your own tool. */
export interface Reach {
  type: string;
  shared?: boolean;
  owner?: string;
}

export const EVERY_REPO = "This project could reach every repository this token can.";

/** A shared GitHub connection linked without a repository pinned reaches everything its
 * token can, in every client's project it's linked to. */
export function reachesEverything(connection: Reach | undefined, scope: Record<string, string>): boolean {
  const shared = Boolean(connection?.shared) || connection?.owner === "you";
  return connection?.type === "github" && shared && !(scope.repo ?? "").trim();
}

export interface RemoteService {
  key: string;
  name: string;
  signin: boolean;
  paste: string | null;
  paste_url: string;
  paste_steps: string;
  /** Shown above the field: a pasted token that ends, say. */
  paste_warning?: string;
  note: string;
  scope: ScopeField[];
  category?: string;
  url_label?: string;
  url_hint?: string;
  local?: boolean;
  needs?: string;
  /** Signs in through your own app of this kind (Settings › Developer apps), like Meta. */
  provider?: string;
  provider_steps?: string;
  provider_scopes?: string[];
  /** The address to paste into that app, or why there isn't one here. */
  redirect?: string;
  redirect_error?: string;
  /** How many apps of that kind you've added. */
  apps?: number;
}

/** Which service's mark a connection shows. */
export function markOf(connection: { type: string; config?: Record<string, unknown> | null }): string {
  const { remote, vendor } = connection.config ?? {};
  return typeof remote === "string" ? remote : typeof vendor === "string" ? vendor : connection.type;
}

/** What the service's plan doesn't allow, if it said so. */
/** A connection that holds agents' notes (Notion memory): never linked to a project. */
export function notesOnly(connection: { type: string; config?: Record<string, unknown> | null }): boolean {
  return connection.type === "vendor" && connection.config?.vendor === "notion_memory";
}

/** A connection that can save email drafts on its own (a Google account has this per tool). */
export function savesDrafts(connection: { type: string; config?: Record<string, unknown> | null }): boolean {
  return connection.type === "gmail" || connection.type === "mailbox" || (connection.type === "vendor" && connection.config?.vendor === "microsoft");
}

export function planLimit(connection: { config?: Record<string, unknown> | null }): string | null {
  const kept = connection.config?.plan_limit as { message?: string } | undefined;
  return kept?.message ?? null;
}

export interface LinkInfo {
  project: string;
  scope: Record<string, string>;
  is_default: boolean;
}

export type LibraryConnection = ConnectionData & { links?: LinkInfo[]; scope_fields?: ScopeField[]; suggested_title?: string | null };

export function serviceName(connection: LibraryConnection, services: RemoteService[] = []): string {
  const remote = typeof connection.config.remote === "string" ? connection.config.remote : null;
  if (remote) return services.find((s) => s.key === remote)?.name ?? remote;
  return SERVICE[connection.type] ?? connection.type;
}

export function scopeText(scope: Record<string, string>, fields: ScopeField[] = []): string {
  const parts = Object.entries(scope).map(([key, value]) => {
    const field = fields.find((f) => f.key === key);
    const several = value.split(",").map((v) => v.trim()).filter(Boolean);
    if (field?.many && several.length > 1) return `${several.length} ${field.label.toLowerCase() === "repository" ? "repositories" : `${field.label.toLowerCase()}s`} (${several.join(", ")})`;
    return `${field?.label ?? key} ${several.join(", ") || value}`;
  });
  return parts.length ? `limited to ${parts.join(", ")}` : "the whole connection";
}

export function ScopeInputs({ fields, value, onChange, connection }: { fields: ScopeField[]; value: Record<string, string>; onChange: (next: Record<string, string>) => void; connection?: Reach }) {
  if (!fields.length) return null;
  return (
    <div className="space-y-2">
      <p className="text-xs text-muted">Narrow what this project can reach. Leave empty for all of it.</p>
      {fields.map((field) =>
        field.many ? (
          <Several key={field.key} field={field} value={value[field.key] ?? ""} onChange={(v) => onChange({ ...value, [field.key]: v })} />
        ) : (
          <Field key={field.key} label={field.label} value={value[field.key] ?? ""} onChange={(v) => onChange({ ...value, [field.key]: v.trim() })} hint={field.hint} />
        ),
      )}
      {reachesEverything(connection, value) ? <p className="text-xs text-warn">{EVERY_REPO}</p> : null}
    </div>
  );
}

/** A field that takes several values (comma-separated when saved): one box each, and
 * "Add another repository". */
function Several({ field, value, onChange }: { field: ScopeField; value: string; onChange: (next: string) => void }) {
  const [rows, setRows] = useState<string[]>(() => {
    const found = value.split(",").map((v) => v.trim()).filter(Boolean);
    return found.length ? found : [""];
  });
  const set = (next: string[]) => {
    setRows(next);
    onChange(next.map((v) => v.trim()).filter(Boolean).join(","));
  };
  return (
    <div className="space-y-2">
      {rows.map((row, i) => (
        <div key={i} className="flex items-end gap-2">
          <div className="flex-1">
            <Field label={i ? `${field.label} ${i + 1}` : field.label} value={row} onChange={(v) => set(rows.map((r, j) => (j === i ? v : r)))} hint={i ? undefined : field.hint} />
          </div>
          {rows.length > 1 ? (
            <Button kind="ghost" onClick={() => set(rows.filter((_, j) => j !== i))} label={`Remove ${row || "this one"}`}>
              Remove
            </Button>
          ) : null}
        </div>
      ))}
      <Button kind="ghost" onClick={() => setRows([...rows, ""])}>
        Add another {field.label.toLowerCase()}
      </Button>
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
          {projectName(link.project)}
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
          <ScopeInputs fields={fields} value={scope} onChange={setScope} connection={connection} />
          <Button kind="primary" onClick={() => save(scope)}>
            Save
          </Button>
        </div>
      ) : null}
      {!editing && reachesEverything(connection, link.scope) ? <p className="text-xs text-warn">{EVERY_REPO} Narrow it to the repositories it needs.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
    </li>
  );
}

/** "No projects yet": connections are used through projects, so say how to get one. */
export function NoProjects({ client }: { client?: string }) {
  const add = client ? `/projects/new?client=${encodeURIComponent(client)}` : "/projects/new";
  return (
    <div className="flex flex-wrap items-center gap-2 rounded-xl border border-dashed border-line p-3 text-sm">
      <span className="text-muted">{client ? `${clientName(client)} has no projects yet.` : "No projects yet."} Connections are used through projects.</span>
      <LinkButton href={add}>Add project</LinkButton>
      <LinkButton href="/projects/new#from-claude">From Claude</LinkButton>
    </div>
  );
}

/** Link a connection to some of its client's projects (a shared tool: any), each
 * narrowed where the service allows. */
export function LinkToProjects({ connection, projects, onDone }: { connection: LibraryConnection; projects: string[]; onDone: () => void }) {
  const linked = new Set((connection.links ?? []).map((l) => l.project));
  const open = projects.filter((p) => !linked.has(p));
  const [picked, setPicked] = useState<Record<string, Record<string, string>>>({}); // project -> scope
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fields = connection.scope_fields ?? [];
  const chosen = Object.keys(picked);
  if (!projects.length) return <NoProjects client={connection.shared ? undefined : connection.owner} />;
  if (!open.length) return <p className="text-sm text-muted">Every one of {connection.shared ? "your" : `${clientName(connection.owner)}'s`} projects already uses it.</p>;

  async function link() {
    setBusy(true);
    setError(null);
    try {
      for (const [project, scope] of Object.entries(picked)) {
        await api(`projects/${project}/connections/${connection.name}`, { method: "PUT", json: { is_default: false, scope: cleaned(scope) } });
      }
      setPicked({});
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <fieldset className="space-y-2 rounded-xl border border-line p-3">
      <legend className="px-1 text-sm font-medium">Link {connectionTitle(connection)} to…</legend>
      {open.map((project) => (
        <div key={project} className="space-y-1">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={project in picked}
              onChange={(e) =>
                setPicked((all) => {
                  const next = { ...all };
                  if (e.target.checked) next[project] = {};
                  else delete next[project];
                  return next;
                })
              }
              className="size-4 accent-[var(--color-accent)]"
            />
            {projectName(project)}
          </label>
          {project in picked && fields.length ? (
            <div className="ml-6">
              <ScopeInputs fields={fields} value={picked[project] ?? {}} onChange={(scope) => setPicked((all) => ({ ...all, [project]: scope }))} connection={connection} />
            </div>
          ) : null}
        </div>
      ))}
      <Button kind="primary" busy={busy} disabled={!chosen.length} onClick={link}>
        {chosen.length > 1 ? `Link to ${chosen.length} projects` : "Link"}
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </fieldset>
  );
}

/** A connection for every project it can reach, new ones too (or only the ones it's linked
 * to): a shared tool, maybe except some clients (their projects, new ones too) and some
 * projects; a client's connection, all of that client's, maybe except some of them. */
function AllProjects({ connection, onChange }: { connection: LibraryConnection; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const all = Boolean(connection.all_projects);
  const clients = connection.except_clients ?? [];
  const projects = connection.except_projects ?? [];
  const whose = connection.shared ? "" : `${clientName(connection.owner)} `;
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
        label={`All ${whose}projects`}
        checked={all}
        onChange={(on) => patch({ all_projects: on })}
        hint={
          !all
            ? `Only the projects below. Turn on to give it to all ${whose}projects, new ones too.`
            : clients.length || projects.length
              ? `${connection.reach}. New projects get it too${connection.shared ? ", unless their client is left out" : ""}.`
              : `All ${whose}projects have it, and new ones get it too. A project's link can still be narrowed below.`
        }
      />
      {all ? <Exceptions clients={clients} projects={projects} owner={connection.shared ? undefined : connection.owner} onSave={patch} /> : null}
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
}

/** "All projects except…": clients (their projects, new ones too) and projects. */
function Exceptions({ clients, projects, owner, onSave }: { clients: string[]; projects: string[]; owner?: string; onSave: (json: Record<string, unknown>) => void }) {
  const known = useApi<{ name: string; owner: string }[]>("projects");
  const scope = useScope();
  const [pick, setPick] = useState("");
  const same = sameClient;
  const allClients = scope.clients.map((c) => c.slug);
  const clientChoices = owner ? [] : allClients.filter((c) => !clients.some((x) => same(x, c)));
  // A project of a client already left out is left out anyway.
  const projectChoices = (known.data ?? []).filter((p) => !projects.includes(p.name) && !clients.some((c) => same(c, p.owner)) && (!owner || same(owner, p.owner))).map((p) => p.name);
  function add(value: string) {
    setPick("");
    if (value.startsWith("client:")) onSave({ except_clients: [...clients, value.slice(7)] });
    else if (value.startsWith("project:")) onSave({ except_projects: [...projects, value.slice(8)] });
  }
  const left = [...clients.map((c) => ({ key: `client:${c}`, label: `${clientName(c)} (client)` })), ...projects.map((p) => ({ key: `project:${p}`, label: projectName(p) }))];
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
            ["", owner ? "Leave out a project…" : left.length ? "Leave out another client or project…" : "Leave out a client or a project…"],
            ...clientChoices.map((c): [string, string] => [`client:${c}`, `${clientName(c)} (client: all their projects, new ones too)`]),
            ...projectChoices.map((p): [string, string] => [`project:${p}`, projectName(p)]),
          ]}
        />
      ) : null}
    </div>
  );
}

/** A connection in its client's library (or a shared tool, in the shared tools): what
 * it is, where it's used, and linking it, at a glance; testing, its tools and changing
 * it under Details. */
export function LibraryCard({ connection, projects, services, onChange }: { connection: LibraryConnection; projects: string[]; services: RemoteService[]; onChange: () => void; compact?: boolean }) {
  // A link to this connection (connectionHref) opens it and scrolls to it.
  const [targeted] = useState(() => typeof window !== "undefined" && window.location.hash.startsWith(`#connections/${encodeURIComponent(connection.name)}`));
  useEffect(() => {
    if (targeted) document.getElementById(`connection-${connection.name}`)?.scrollIntoView({ block: "center" });
  }, [targeted, connection.name]);
  // ...and with /link, opens its Link to project… right away.
  const [linking, setLinking] = useState(() => typeof window !== "undefined" && window.location.hash === `#connections/${encodeURIComponent(connection.name)}/link`);
  const [open, setOpen] = useState(targeted && !linking);
  const [title, setTitle] = useState(connectionTitle(connection));
  const [expiry, setExpiry] = useState(typeof connection.config.expires_at === "string" ? connection.config.expires_at : "");
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const remote = typeof connection.config.remote === "string";
  const fromCatalog = typeof connection.config.registry === "string";
  const imported = connection.type === "mcp" && !remote && !fromCatalog;
  const kind = serviceName(connection, services);
  const mark = markOf(connection);

  async function patch(json: Record<string, unknown>, done: string) {
    setError(null);
    try {
      await api(`connections/${connection.name}`, { method: "PATCH", json });
      setSaved(done);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function replace() {
    setError(null);
    try {
      await api(`connections/${connection.name}/secret`, { method: "PUT", json: { secret } });
      setSecret("");
      setSaved("Saved. Test the connection to check it.");
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
  const expires = typeof connection.config.expires_at === "string" ? connection.config.expires_at : "";
  const left = expires ? Math.ceil((new Date(expires).getTime() - Date.now()) / 86_400_000) : null;
  return (
    <div id={`connection-${connection.name}`} className="space-y-2 rounded-2xl border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <ServiceMark id={mark} database={connection.type === "supabase"} />
        <span className="font-medium">{connectionTitle(connection)}</span>
        {/* A Google account's writes all ask first, whatever the data: no kind-of-data badge. */}
        {connection.type === "google" || connection.shared ? null : <EnvBadge env={connection.env} />}
        {kind !== connectionTitle(connection) ? <span className="text-xs text-muted">{kind}</span> : null}
        {connection.shared ? <Badge tone="accent">your tool</Badge> : null}
        {!connection.all_projects && !links.length && !notesOnly(connection) ? <Badge tone="warn">Not linked to any project</Badge> : null}
        {planLimit(connection) ? <Badge tone="warn">Plan limit</Badge> : connection.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
        {connection.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
        {left !== null && left <= 14 ? <Badge tone={left <= 7 ? "bad" : "warn"}>{left < 0 ? "key expired" : `key expires in ${left} day${left === 1 ? "" : "s"}`}</Badge> : null}
        {connection.last_test_at ? <span className="ml-auto text-xs text-muted">tested {timeAgo(connection.last_test_at)}</span> : null}
      </div>
      {planLimit(connection) ? <Alert tone="warn">{planLimit(connection)}</Alert> : null}
      {connection.suggested_title ? (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className="text-muted">Another connection has this name too.</span>
          <Button onClick={() => void patch({ title: connection.suggested_title }, "Renamed.")}>Rename it to “{connection.suggested_title}”</Button>
        </div>
      ) : null}
      {connection.type === "airtable" ? <AirtableCovers connection={connection} services={services} /> : null}
      {/* Why the last test failed, without opening anything. */}
      {connection.last_test_ok === false && connection.last_test_message ? <p className="text-sm text-bad">{connection.last_test_message}</p> : null}
      {notesOnly(connection) ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">It holds agents&apos; notes, so it isn&apos;t linked to projects and no task calls it. Choose it for every project here, or in a project&apos;s Settings › Memory.</p>
          <EveryProject connection={connection.name} />
        </div>
      ) : null}
      <p className={notesOnly(connection) ? "hidden" : "text-sm"}>
        {connection.all_projects ? (
          <span>{connection.reach || "Every project, new ones too"}</span>
        ) : links.length ? (
          <>
            <span className="text-muted">Used by </span>
            {links.map((link, i) => (
              <span key={link.project}>
                {i ? ", " : ""}
                <Link href={projectHref(link.project)} className="text-accent">
                  {projectName(link.project)}
                </Link>
                {Object.keys(link.scope).length ? <span className="text-muted"> ({scopeText(link.scope, connection.scope_fields)})</span> : null}
              </span>
            ))}
          </>
        ) : (
          <span className="text-muted">Not linked to any project, so no task can use it. Link it to the projects that should.</span>
        )}
      </p>
      <div className="flex flex-wrap gap-2">
        {connection.all_projects || notesOnly(connection) ? null : (
          <Button kind={links.length ? "secondary" : "primary"} onClick={() => setLinking(!linking)}>
            {linking ? "Cancel" : links.length ? "Link to project…" : "Link"}
          </Button>
        )}
        <Button kind="ghost" onClick={() => setOpen(!open)}>
          {open ? "Hide details" : "Details"}
        </Button>
      </div>
      {linking ? (
        <LinkToProjects
          connection={connection}
          projects={projects}
          onDone={() => {
            setLinking(false);
            onChange();
          }}
        />
      ) : null}
      {open ? (
        <div className="space-y-3 border-t border-line pt-3">
          {notesOnly(connection) ? null : <AllProjects connection={connection} onChange={onChange} />}
          {links.length ? (
            <ul className="space-y-1">
              {links.map((link) => (
                <LinkRow key={link.project} connection={connection} link={link} onChange={onChange} />
              ))}
            </ul>
          ) : null}
          {connection.config.package ? <LocalUpdate connection={connection.name} client={connection.owner} onDone={onChange} /> : null}
          <TestButton name={connection.name} onDone={() => onChange()} />
          {connection.type === "mcp" ? <McpTools name={connection.name} onChange={onChange} /> : null}
          {connection.type === "google" ? <GoogleAccount name={connection.name} onChange={onChange} /> : null}
          {savesDrafts(connection) ? (
            <Toggle
              label="Hold drafts for my OK too"
              checked={connection.config.hold_drafts === true}
              onChange={(on) => void patch({ hold_drafts: on }, on ? "Drafts wait for your OK." : "Drafts are saved without asking.")}
              hint="Off: saving a draft runs and is logged, since it reaches nobody, and the task's result links it. Sending always waits for your OK."
            />
          ) : null}
          <Details label="Change">
            <div className="space-y-3">
              <Field label="Name" value={title} onChange={setTitle} hint={`As it shows in Ringkeeper. The AI knows it as ${connection.name}.`} />
              <Button disabled={!title.trim() || title.trim() === connectionTitle(connection)} onClick={() => patch({ title: title.trim() }, "Renamed.")}>
                Rename
              </Button>
              <Field label="Its key expires on (if you know)" type="date" value={expiry} onChange={setExpiry} hint="You'll hear 7 days before. GitHub says on its own." />
              <Button disabled={expiry === expires} onClick={() => patch({ expires_at: expiry }, "Saved.")}>
                Save the date
              </Button>
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
              {connection.shared ? null : (
                <ConfirmButton
                  question={`Make ${connectionTitle(connection)} your own tool? Its projects keep it, and you can link it to other clients' projects too. Narrow each of those links (for n8n, to one workflow tag) so a client reaches only its own.`}
                  confirm="Make it my tool"
                  onConfirm={() => patch({ shared: true }, "It's your own tool now. Link it to a client's project, then narrow that link under Details.")}
                >
                  Make it my own tool
                </ConfirmButton>
              )}
              <ConfirmButton question={`Remove ${connectionTitle(connection)}? Its projects lose it too.`} confirm="Remove it" onConfirm={remove}>
                Remove connection
              </ConfirmButton>
            </div>
          </Details>
        </div>
      ) : null}
      {saved ? <Alert tone="good">{saved}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Connect a service you sign in to (or paste a token for). With `project`, the new
 * connection is linked to it, and the link can be narrowed right away. */
/** A built-in Airtable connection: what it covers, and Airtable's own server for what it
 * doesn't (automations, interfaces), for the same client and base. */
function AirtableCovers({ connection, services }: { connection: LibraryConnection; services: RemoteService[] }) {
  const own = services.find((s) => s.key === "airtable");
  const base = (connection.links ?? []).map((l) => l.scope.base ?? l.scope.base_id ?? "").find(Boolean) ?? "";
  const query = new URLSearchParams({ service: "airtable", ...(connection.shared ? { kind: "shared" } : { client: connection.owner }), ...(base ? { base } : {}) });
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span className="text-muted">Reads and changes bases, tables and records, with undo. It can&apos;t see automations or interfaces.</span>
      {own ? (
        <Link href={`/connections/new?${query}`} className="font-medium text-accent">
          Add Airtable&apos;s own server for automations
        </Link>
      ) : null}
    </div>
  );
}

export function ConnectRemote({ service, client, project, shared = false, onAdded, startScope }: { service: RemoteService; client: string; project?: string; shared?: boolean; onAdded: (name: string) => void; startScope?: Record<string, string> }) {
  const [typed, setTyped] = useState<string | null>(null); // null: the suggested title
  const [env, setEnv] = useState("");
  // A base already pinned on the built-in connection, offered again here.
  const [scope, setScope] = useState<Record<string, string>>(startScope ?? {});
  const [token, setToken] = useState("");
  const [url, setUrl] = useState("");
  const [pasting, setPasting] = useState(!service.signin);
  const [waiting, setWaiting] = useState<string | null>(null); // the name sent, until it's there
  const [signin, setSignin] = useState<string | null>(null); // a sign-in through your own app
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taken = useTakenNames();
  const title = typed ?? defaultTitle(service.name, "", shared ? undefined : client);
  const name = nameFor(title, shared ? undefined : client, taken);
  const body = { remote: service.key, url: url.trim() || null, name, title: title.trim(), owner: client, env, project: project ?? null, scope: cleaned(scope), shared };
  const needsUrl = Boolean(service.url_label) && !url.trim();
  const nothingElse = !service.signin && !service.paste && !service.local; // the address is all it takes

  // After the service's page opens: the connection appears once its sign-in finishes.
  useEffect(() => {
    if (!waiting) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<ConnectionData[]>("connections")
        .then((all) => {
          if (all.some((c) => c.name === waiting)) {
            setWaiting(null);
            onAdded(waiting);
          } else if (Date.now() > deadline) setWaiting(null);
        })
        .catch(() => undefined);
      // Through your own app, a sign-in that failed says why.
      if (signin) {
        api<{ state: string; error: string | null }>(`developer-apps/signin/${encodeURIComponent(signin)}`)
          .then((found) => {
            if (found.state === "failed") {
              setWaiting(null);
              setError(found.error ?? "The sign-in didn't finish. Try again.");
            }
          })
          .catch(() => undefined);
      }
    }, 2000);
    return () => clearInterval(timer);
  }, [waiting, signin, onAdded]);

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const { url, state } = await api<{ url: string; state?: string }>("connections/signin", { json: body });
      window.open(url, "_blank", "noopener");
      setSignin(state ?? null);
      setWaiting(name);
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
      await api("connections/remote", { json: nothingElse ? { ...body, open: true } : { ...body, token } });
      setToken("");
      onAdded(name);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (service.local) {
    return (
      <div className="space-y-2 rounded-xl border border-line p-3 text-sm">
        {service.note ? <p className="text-muted">{service.note}</p> : null}
        <p>Sign in with {service.name}&apos;s command line tool on this computer. Then connect that login under <strong>Signed in on this computer</strong>, on the Connectors page.</p>
      </div>
    );
  }
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      {service.note ? <p className="text-sm text-muted">{service.note}</p> : null}
      <EnvChoice value={env} onChange={setEnv} tool />
      <Field label="Name" value={title} onChange={setTyped} hint="As it shows in Ringkeeper. Change it if you like." />
      {service.url_label ? <Field label={service.url_label} value={url} onChange={setUrl} hint={service.url_hint} /> : null}
      {project ? <ScopeInputs fields={service.scope} value={scope} onChange={setScope} /> : null}
      {waiting ? <Alert tone="good">Finish signing in on {service.name}&apos;s page that opened in your browser. This updates when it&apos;s done.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {service.provider && !pasting ? <ThroughYourApp service={service} /> : null}
      {service.signin && !pasting ? (
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy} disabled={!title.trim() || !env || needsUrl || (Boolean(service.provider) && !service.apps)} onClick={signIn}>
            Sign in with {service.provider === "meta" ? "Meta" : service.name}
          </Button>
          {service.paste ? (
            <Button kind="ghost" onClick={() => setPasting(true)}>
              Use a token instead
            </Button>
          ) : null}
        </div>
      ) : null}
      {nothingElse ? (
        <Button kind="primary" busy={busy} disabled={!title.trim() || !env || needsUrl} onClick={paste}>
          Connect
        </Button>
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
          {service.paste_warning ? <Alert>{service.paste_warning}</Alert> : null}
          <Field label={service.paste} type="password" value={token} onChange={setToken} />
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!title.trim() || !env || !token.trim() || needsUrl} onClick={paste}>
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

/** A server that signs in through your own app (Meta's ads server): what the app needs, and the
 * exact address to paste into it. */
function ThroughYourApp({ service }: { service: RemoteService }) {
  const meta = service.provider === "meta";
  return (
    <div className="space-y-2 rounded-xl bg-accent-soft p-3 text-sm">
      {service.provider_steps ? <p>{service.provider_steps}</p> : null}
      {service.redirect ? (
        <div className="space-y-1">
          <p>{meta ? "In the app's Facebook Login for Business settings, paste this as a Valid OAuth Redirect URI:" : "Paste this into your app as its redirect address:"}</p>
          <code className="block select-all break-all rounded-lg border border-line bg-surface px-2 py-1">{service.redirect}</code>
        </div>
      ) : service.redirect_error ? (
        <Alert>{service.redirect_error}</Alert>
      ) : null}
      {service.provider_scopes?.length ? <p className="text-muted">It asks for: {service.provider_scopes.join(", ")}.</p> : null}
      {!service.apps ? (
        <Alert>
          Add your {meta ? "Meta" : ""} app first, in{" "}
          <a href="/settings#developer-apps" className="underline">
            Settings › Developer apps
          </a>
          .
        </Alert>
      ) : null}
    </div>
  );
}

export function useLibrary(client: string) {
  const connections = useApi<LibraryConnection[]>("connections");
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const services = useApi<RemoteService[]>("remote-services");
  const same = (owner: string) => owner.trim().toLowerCase() === client.trim().toLowerCase();
  const theirs = (projects.data ?? []).filter((p) => same(p.owner)).map((p) => p.name);
  return {
    connections: (connections.data ?? []).filter((c) => !c.shared && same(c.owner)),
    /** Your own tools their projects use: not theirs, but what those projects have. */
    tools: (connections.data ?? []).filter((c) => c.shared && c.projects.some((p) => theirs.includes(p))),
    projects: theirs,
    services: services.data ?? [],
    loading: connections.loading || projects.loading,
    error: connections.error ?? projects.error,
    reload: () => {
      connections.reload();
      projects.reload();
    },
  };
}

