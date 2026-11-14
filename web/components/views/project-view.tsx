"use client";

import Link from "@/components/link";
import { BackLink } from "@/components/history-arrows";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { EVERY_REPO, notesOnly, reachesEverything, ScopeInputs, scopeText, type LibraryConnection, type ScopeField } from "@/components/connection-library";
import { Catalog, ServiceMark } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { connectionTitle } from "@/components/connection-form";
import { ProjectScan } from "@/components/conveniences";
import { ProjectForm, TestButton, type ProjectData } from "@/components/forms";
import { ProjectClaudePlace } from "@/components/claude-place";
import { NotesWhere, ProjectMemory } from "@/components/memory";
import { Alert, Button, ConfirmButton, Details, EnvBadge, Field, Loading, Section, Select, Tabs, Toggle, type Tab } from "@/components/ui";
import { Agents, type AgentData } from "@/components/agents";
import { NewRoutine, RoutineList } from "@/components/routines";
import { ScopedTasks, useTaskSummary } from "@/components/scoped-tasks";
import { api, useApi } from "@/lib/client/api";
import { clientHref, projectHref, routineHref } from "@/lib/client/routes";
import { clientName, projectName } from "@/lib/client/scope";
import { AUTONOMY, SERVICE, slugify } from "@/lib/client/words";

interface Bound {
  name: string;
  title?: string | null;
  type: string;
  env: string;
  is_default: boolean;
  scope?: Record<string, string>;
}

/** One linked connection: what it's narrowed to, default or not, unlink. */
function BoundRow({ project, instance, shared, fields, onChange }: { project: string; instance: Bound; shared: boolean; fields: ScopeField[]; onChange: () => void }) {
  const [editing, setEditing] = useState(false);
  const [scope, setScope] = useState(instance.scope ?? {});
  const [error, setError] = useState<string | null>(null);
  async function put(body: Record<string, unknown> | null) {
    setError(null);
    try {
      const path = `projects/${project}/connections/${instance.name}`;
      await (body === null ? api(path, { method: "DELETE" }) : api(path, { method: "PUT", json: body }));
      setEditing(false);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{connectionTitle(instance)}</span>
        {instance.type === "google" || shared ? null : <EnvBadge env={instance.env} />}
        <span className="text-xs text-muted">{SERVICE[instance.type] ?? instance.type}</span>
        <span className="text-xs text-muted">{scopeText(instance.scope ?? {}, fields)}</span>
        <div className="ml-auto flex items-center gap-3">
          {instance.env !== "prod" ? <Toggle label="Default" checked={instance.is_default} onChange={(on) => put({ is_default: on })} /> : null}
          {fields.length ? (
            <Button kind="ghost" onClick={() => setEditing(!editing)}>
              {editing ? "Cancel" : "Narrow"}
            </Button>
          ) : null}
          <Button kind="ghost" onClick={() => put(null)}>
            Unlink
          </Button>
        </div>
      </div>
      {editing ? (
        <div className="space-y-2">
          <ScopeInputs fields={fields} value={scope} onChange={setScope} connection={instance} />
          <Button kind="primary" onClick={() => put({ is_default: instance.is_default, scope: Object.fromEntries(Object.entries(scope).filter(([, v]) => v)) })}>
            Save
          </Button>
        </div>
      ) : null}
      {!editing && reachesEverything(instance, instance.scope ?? {}) ? <p className="text-xs text-warn">{EVERY_REPO} Narrow it to the repositories it needs.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

interface Detail extends ProjectData {
  instances: Bound[];
  paused?: boolean;
}

export function ProjectView({ name }: { name: string }) {
  const router = useRouter();
  const project = useApi<Detail>(`projects/${name}`);
  const tasksSummary = useTaskSummary({ project: name });
  const connections = useApi<LibraryConnection[]>("connections");
  const [linking, setLinking] = useState(false);
  const [picked, setPicked] = useState<Record<string, Record<string, string>>>({}); // connection -> scope
  const [connecting, setConnecting] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(action: () => Promise<unknown>) {
    setError(null);
    try {
      await action();
      project.reload();
      connections.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    setError(null);
    try {
      await api(`projects/${name}`, { method: "DELETE" });
      router.push("/projects");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (project.error) return <Alert>{project.error}</Alert>;
  if (!project.data) return <Loading />;
  const bound = new Set(project.data.instances.map((i) => i.name));
  // A client's connection is shared only among that client's projects; a shared tool goes anywhere.
  const client = project.data.owner.trim().toLowerCase();
  const byName = new Map((connections.data ?? []).map((c) => [c.name, c]));
  const available = (connections.data ?? []).filter((c) => !bound.has(c.name) && !notesOnly(c) && (c.shared || c.owner.trim().toLowerCase() === client));

  const instances = project.data.instances;
  const production = instances.filter((i) => i.env === "prod").length;
  const connectionSummary = `${instances.length} connection${instances.length === 1 ? "" : "s"}${production ? ` · ${production} production` : ""}`;
  const about = project.data;
  const autonomy = AUTONOMY[about.autonomy]?.join(": ") ?? about.autonomy;
  const title = about.title;

  const chosen = Object.keys(picked);
  const connectionsTab = (
    <Section
      id="connections"
      title="Connections"
      summary={connectionSummary}
      action={
        <Button kind={instances.length ? "secondary" : "primary"} onClick={() => setLinking(!linking)}>
          {linking ? "Cancel" : "Link a connection"}
        </Button>
      }
    >
      <p className="text-sm text-muted">
        The services this project may use: from{" "}
        <Link href={clientHref(about.owner)} className="text-accent">
          {clientName(about.owner)}&apos;s connections
        </Link>
        , and your own tools. A link can be narrowed to one database, base or project. A default is used when a request doesn&apos;t name one; production can never be the default, so live data is always named on purpose.
      </p>
      {linking ? (
        <fieldset className="space-y-2 rounded-xl border border-line p-3">
          <legend className="px-1 text-sm font-medium">Link to {projectName(name)}</legend>
          {available.length ? (
            <>
              {available.map((c) => (
                <div key={c.name} className="space-y-1">
                  <label className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={c.name in picked}
                      onChange={(e) =>
                        setPicked((all) => {
                          const next = { ...all };
                          if (e.target.checked) next[c.name] = {};
                          else delete next[c.name];
                          return next;
                        })
                      }
                      className="size-4 accent-[var(--color-accent)]"
                    />
                    <ServiceMark id={typeof c.config.remote === "string" ? c.config.remote : c.type} database={c.type === "supabase"} />
                    <span className="font-medium">{connectionTitle(c)}</span>
                    {c.shared ? <span className="text-xs text-muted">your tool</span> : <EnvBadge env={c.env} />}
                  </label>
                  {c.name in picked && (c.scope_fields ?? []).length ? (
                    <div className="ml-6">
                      <ScopeInputs fields={c.scope_fields ?? []} value={picked[c.name] ?? {}} onChange={(next) => setPicked((all) => ({ ...all, [c.name]: next }))} connection={c} />
                    </div>
                  ) : null}
                </div>
              ))}
              <Button
                kind="primary"
                disabled={!chosen.length}
                onClick={() =>
                  run(async () => {
                    for (const [connection, narrowed] of Object.entries(picked)) {
                      const scope = Object.fromEntries(Object.entries(narrowed).filter(([, v]) => v));
                      await api(`projects/${name}/connections/${connection}`, { method: "PUT", json: { is_default: false, scope } });
                    }
                    setPicked({});
                    setLinking(false);
                  })
                }
              >
                {chosen.length > 1 ? `Link ${chosen.length} connections` : "Link"}
              </Button>
            </>
          ) : (
            <p className="text-sm text-muted">Every one of {clientName(about.owner)}&apos;s connections and your tools is linked already. Connect a new service below.</p>
          )}
        </fieldset>
      ) : null}
      <ProjectScan
        project={name}
        owner={about.owner}
        onChanged={() => {
          project.reload();
          connections.reload();
        }}
      />
      {instances.length ? null : <p className="text-sm text-muted">No connections linked yet.{available.length ? " Link one with Link a connection, or connect a new service below." : " Connect a service below."}</p>}
      {instances.map((instance) => (
        <BoundRow key={instance.name} project={name} instance={instance} shared={byName.get(instance.name)?.shared ?? false} fields={byName.get(instance.name)?.scope_fields ?? []} onChange={() => run(async () => undefined)} />
      ))}
      {added ? (
        <ConnectionAdded
          name={added}
          onLinked={() => {
            project.reload();
            connections.reload();
          }}
          onAnother={() => {
            setAdded(null);
            setConnecting(true);
          }}
          onDone={() => {
            setAdded(null);
            project.reload();
            connections.reload();
          }}
        />
      ) : connecting ? (
        <div className="space-y-3 rounded-xl border border-line p-3">
          <h3 className="font-medium">New connection for {projectName(name)}</h3>
          <Catalog
            client={about.owner}
            project={name}
            kind="client"
            onAdded={(connection) => {
              setConnecting(false);
              setAdded(connection);
              project.reload();
              connections.reload();
            }}
          />
          <Button kind="ghost" onClick={() => setConnecting(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button kind="ghost" onClick={() => setConnecting(true)}>
          Connect a new service
        </Button>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </Section>
  );

  const tabs: Tab[] = [
    {
      key: "overview",
      label: "Overview",
      content: () => (
        <>
        <div className="mb-4">
          <ProjectScan
            project={name}
            owner={about.owner}
            onChanged={() => {
              project.reload();
              connections.reload();
            }}
          />
        </div>
        <Section id="about" title="About" summary={autonomy}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Client</dt>
            <dd>
              <Link href={clientHref(about.owner)} className="text-accent">
                {clientName(about.owner)}
              </Link>
            </dd>
            <dt className="text-muted">What it is</dt>
            <dd>{about.description || <span className="text-muted">Not described yet (Settings tab)</span>}</dd>
            {about.aliases.length ? (
              <>
                <dt className="text-muted">Also called</dt>
                <dd>{about.aliases.join(", ")}</dd>
              </>
            ) : null}
            {about.workspace_path ? (
              <>
                <dt className="text-muted">Folder</dt>
                <dd className="font-mono text-xs break-all">{about.workspace_path}</dd>
              </>
            ) : null}
            <dt className="text-muted">Connections</dt>
            <dd>{instances.length ? instances.map(connectionTitle).join(", ") : "None yet. Link one in the Connections tab."}{production ? ` (${production} production)` : ""}</dd>
            <dt className="text-muted">Works</dt>
            <dd>{autonomy}</dd>
            <dt className="text-muted">Notes</dt>
            <dd>
              <NotesWhere project={name} />
            </dd>
            <dt className="text-muted">Web access</dt>
            <dd>{about.web_allowed ? "On: its tasks can look things up on the web" : "Off: its tasks can't use the web"}</dd>
          </dl>
        </Section>
        </>
      ),
    },
    { key: "connections", label: "Connections", count: instances.length, content: () => connectionsTab },
    {
      key: "agents",
      label: "Agents",
      content: () => <Agents project={name} linked={instances.map((i) => ({ name: i.name, type: i.type, shared: byName.get(i.name)?.shared }))} />,
    },
    { key: "routines", label: "Routines", content: () => <ProjectRoutines project={name} paused={Boolean(project.data?.paused)} onPaused={project.reload} /> },
    { key: "tasks", label: "Tasks", count: tasksSummary.count, content: () => <ScopedTasks project={name} /> },
    {
      key: "settings",
      label: "Settings",
      content: () => (
        <>
          <Section id="project-settings" title="About this project" summary={about.description || undefined}>
            <ProjectForm existing={about} onSaved={() => project.reload()} />
          </Section>
          <ProjectClaudePlace project={name} />
          <ProjectMemory project={name} />
          <Section id="project-name" title="Short name" summary="Used in links and by the AI" defaultOpen={false}>
            <RenameProject name={name} />
          </Section>
          <ConfirmButton question={`Remove ${title}? Its connections stay; only the link to them goes.`} confirm={`Remove ${title}`} onConfirm={remove}>
            Remove project
          </ConfirmButton>
        </>
      ),
    },
  ];

  return (
    <>
      <BackLink href="/projects">‹ Clients & projects</BackLink>
      <h1 className="mt-2 mb-1 text-xl font-semibold">{title}</h1>
      <p className="mb-4 text-sm text-muted">
        {clientName(about.owner)}&apos;s project · {instances.length} connection{instances.length === 1 ? "" : "s"}
        {production ? ` (${production} production)` : ""}
        {tasksSummary.running ? ` · ${tasksSummary.running} running` : ""}
      </p>
      <Tabs id={`project-${name}`} tabs={tabs} />
    </>
  );
}

/** The short name links and the AI use (its title is in About this project). The old
 * one stays as another name, so asking by it still finds the project. */
function RenameProject({ name }: { name: string }) {
  const router = useRouter();
  const [typed, setTyped] = useState(name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const next = slugify(typed);

  async function rename() {
    setBusy(true);
    setError(null);
    try {
      await api(`projects/${name}/rename`, { json: { name: next } });
      router.replace(projectHref(next));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <Field label="Short name" value={typed} onChange={setTyped} hint={next && next !== typed ? `Saved as ${next}. Its tasks, approvals and links follow; the old one stays as another name.` : "You rarely need this: the project's name is under About this project. Its tasks, approvals and links follow; the old one stays as another name."} />
      <Button kind="primary" busy={busy} disabled={!next || next === name} onClick={rename}>
        Rename
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Project › Routines: its routines, adding one (from a template), and pausing them
 * all for this project. */
function ProjectRoutines({ project, paused, onPaused }: { project: string; paused: boolean; onPaused: () => void }) {
  const router = useRouter();
  const agents = useApi<AgentData[]>(`projects/${project}/agents`);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <div className="space-y-4">
      {error ? <Alert>{error}</Alert> : null}
      <Toggle
        label={`Pause ${projectName(project)}'s routines`}
        checked={paused}
        onChange={(on) => void api(`projects/${project}/routines/paused`, { json: { paused: on } }).then(onPaused, () => setError("Couldn't change that. Try again in a moment."))}
        hint="None of this project's routines runs on its own while paused."
      />
      <RoutineList project={project} />
      {adding ? (
        <div className="rounded-xl border border-line p-3">
          <NewRoutine project={project} agents={agents.data ?? []} onCreated={(id) => router.push(routineHref(id))} />
        </div>
      ) : (
        <Button kind="primary" onClick={() => setAdding(true)}>
          Add a routine
        </Button>
      )}
    </div>
  );
}
