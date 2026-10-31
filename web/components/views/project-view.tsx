"use client";

import Link from "@/components/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ScopeInputs, scopeText, type LibraryConnection, type ScopeField } from "@/components/connection-library";
import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { ConnectionForm, ProjectForm, TestButton, type ProjectData } from "@/components/forms";
import { Alert, Button, Details, EnvBadge, Loading, Section, Select, Tabs, Toggle, type Tab } from "@/components/ui";
import { ScopedTasks, useTaskSummary } from "@/components/scoped-tasks";
import { api, useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { AUTONOMY, SERVICE } from "@/lib/client/words";

interface Bound {
  name: string;
  type: string;
  env: string;
  is_default: boolean;
  scope?: Record<string, string>;
}

/** One linked connection: what it's narrowed to, default or not, unlink. */
function BoundRow({ project, instance, fields, onChange }: { project: string; instance: Bound; fields: ScopeField[]; onChange: () => void }) {
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
        <span className="font-medium">{instance.name}</span>
        {instance.type === "google" ? null : <EnvBadge env={instance.env} />}
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
            Remove
          </Button>
        </div>
      </div>
      {editing ? (
        <div className="space-y-2">
          <ScopeInputs fields={fields} value={scope} onChange={setScope} />
          <Button kind="primary" onClick={() => put({ is_default: instance.is_default, scope: Object.fromEntries(Object.entries(scope).filter(([, v]) => v)) })}>
            Save
          </Button>
        </div>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

interface Detail extends ProjectData {
  instances: Bound[];
}

export function ProjectView({ name }: { name: string }) {
  const router = useRouter();
  const project = useApi<Detail>(`projects/${name}`);
  const tasksSummary = useTaskSummary({ project: name });
  const connections = useApi<LibraryConnection[]>("connections");
  const [adding, setAdding] = useState("");
  const [scope, setScope] = useState<Record<string, string>>({});
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
    if (!window.confirm(`Remove ${name}? Its connections stay; only the link to them goes.`)) return;
    await run(() => api(`projects/${name}`, { method: "DELETE" }));
    router.push("/projects");
  }

  if (project.error) return <Alert>{project.error}</Alert>;
  if (!project.data) return <Loading />;
  const bound = new Set(project.data.instances.map((i) => i.name));
  // A client's connection is shared only among that client's projects; a shared tool goes anywhere.
  const client = project.data.owner.trim().toLowerCase();
  const byName = new Map((connections.data ?? []).map((c) => [c.name, c]));
  const available = (connections.data ?? []).filter((c) => !bound.has(c.name) && (c.shared || c.owner.trim().toLowerCase() === client));

  const instances = project.data.instances;
  const production = instances.filter((i) => i.env === "prod").length;
  const connectionSummary = `${instances.length}${production ? ` · ${production} production` : ""}`;
  const about = project.data;
  const autonomy = AUTONOMY[about.autonomy]?.join(": ") ?? about.autonomy;

  const connectionsTab = (
    <Section id="connections" title="Connections" summary={connectionSummary}>
      <p className="text-sm text-muted">
        The services this project may use: from{" "}
        <Link href={clientHref(about.owner)} className="text-accent">
          {about.owner}&apos;s connections
        </Link>
        , and your shared tools. A link can be narrowed to one database, base or project. A default is used when a request doesn&apos;t name one; production can never be the default, so live data is always named on purpose.
      </p>
      {instances.map((instance) => (
        <BoundRow key={instance.name} project={name} instance={instance} fields={byName.get(instance.name)?.scope_fields ?? []} onChange={() => run(async () => undefined)} />
      ))}
      {added ? (
        <ConnectionAdded
          name={added}
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
          <h3 className="font-medium">New connection for {name}</h3>
          <Catalog
            client={about.owner}
            project={name}
            onAdded={(connection) => {
              setConnecting(false);
              setAdded(connection);
              project.reload();
              connections.reload();
            }}
          />
          <Details label="Other services (a database, n8n, Twilio, Gmail…)">
            <ConnectionForm
              client={about.owner}
              project={name}
              onSaved={(connection) => {
                setConnecting(false);
                setAdded(connection.name);
                project.reload();
                connections.reload();
              }}
            />
          </Details>
          <Button kind="ghost" onClick={() => setConnecting(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button kind="primary" onClick={() => setConnecting(true)}>
          Connect a service
        </Button>
      )}
      {available.length ? (
        <div className="space-y-2 rounded-xl border border-line p-3">
          <Select
            label={`Or link one of ${about.owner}'s connections, or a shared tool`}
            value={adding}
            onChange={(next) => {
              setAdding(next);
              setScope({});
            }}
            options={[["", "Choose…"], ...available.map((c): [string, string] => [c.name, c.shared ? `${c.name} (shared tool)` : `${c.name} (${c.env === "prod" ? "production" : c.env})`])]}
          />
          {adding ? <ScopeInputs fields={byName.get(adding)?.scope_fields ?? []} value={scope} onChange={setScope} /> : null}
          <Button
            kind="primary"
            disabled={!adding}
            onClick={() =>
              run(async () => {
                const narrowed = Object.fromEntries(Object.entries(scope).filter(([, v]) => v));
                await api(`projects/${name}/connections/${adding}`, { method: "PUT", json: { is_default: false, scope: narrowed } });
                setAdding("");
                setScope({});
              })
            }
          >
            Link
          </Button>
        </div>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
    </Section>
  );

  const tabs: Tab[] = [
    {
      key: "overview",
      label: "Overview",
      content: () => (
        <Section id="about" title="About" summary={autonomy}>
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
            <dt className="text-muted">Client</dt>
            <dd>
              <Link href={clientHref(about.owner)} className="text-accent">
                {about.owner}
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
            <dd>{instances.length ? instances.map((i) => i.name).join(", ") : "None yet"}{production ? ` (${production} production)` : ""}</dd>
            <dt className="text-muted">Works</dt>
            <dd>{autonomy}</dd>
          </dl>
        </Section>
      ),
    },
    { key: "connections", label: "Connections", count: instances.length, content: () => connectionsTab },
    { key: "tasks", label: "Tasks", count: tasksSummary.count, content: () => <ScopedTasks project={name} /> },
    {
      key: "settings",
      label: "Settings",
      content: () => (
        <>
          <Section id="project-settings" title="About this project" summary={about.description || undefined}>
            <ProjectForm existing={about} onSaved={() => project.reload()} />
          </Section>
          <Button kind="danger" onClick={remove}>
            Remove project
          </Button>
        </>
      ),
    },
  ];

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-1 text-xl font-semibold">{name}</h1>
      <p className="mb-4 text-sm text-muted">
        {about.owner}&apos;s project · {connectionSummary} connection{instances.length === 1 ? "" : "s"}
        {tasksSummary.running ? ` · ${tasksSummary.running} running` : ""}
      </p>
      <Tabs id={`project-${name}`} tabs={tabs} />
    </>
  );
}
