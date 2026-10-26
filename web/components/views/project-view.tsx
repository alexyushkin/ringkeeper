"use client";

import Link from "@/components/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { RemoteServices, ScopeInputs, scopeText, type LibraryConnection, type ScopeField } from "@/components/connection-library";
import { ConnectionForm, ProjectForm, TestButton, type ProjectData } from "@/components/forms";
import { Alert, Button, Card, Details, EnvBadge, Loading, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { SERVICE } from "@/lib/client/words";

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
        <EnvBadge env={instance.env} />
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
  // A connection is shared only among one client's projects.
  const client = project.data.owner.trim().toLowerCase();
  const byName = new Map((connections.data ?? []).map((c) => [c.name, c]));
  const available = (connections.data ?? []).filter((c) => !bound.has(c.name) && c.owner.trim().toLowerCase() === client);

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-4 text-xl font-semibold">{name}</h1>

      <Card className="mb-4 space-y-3">
        <h2 className="font-semibold">Connections</h2>
        <p className="text-sm text-muted">
          The services this project may use, from{" "}
          <Link href={clientHref(project.data.owner)} className="text-accent">
            {project.data.owner}&apos;s connections
          </Link>
          . A link can be narrowed to one database, base or project. A default is used when a request doesn&apos;t name one; production can never be the default, so live data is always named on purpose.
        </p>
        {project.data.instances.map((instance) => (
          <BoundRow key={instance.name} project={name} instance={instance} fields={byName.get(instance.name)?.scope_fields ?? []} onChange={() => run(async () => undefined)} />
        ))}
        {added ? (
          <div className="space-y-2 rounded-xl border border-line p-3">
            <Alert tone="good">Added {added}. Its secret is stored encrypted and won&apos;t be shown again.</Alert>
            <TestButton name={added} />
            <Button kind="ghost" onClick={() => setAdded(null)}>
              Done
            </Button>
          </div>
        ) : connecting ? (
          <div className="space-y-3 rounded-xl border border-line p-3">
            <h3 className="font-medium">New connection for {name}</h3>
            <RemoteServices
              client={project.data.owner}
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
                client={project.data.owner}
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
              label={`Or link one of ${project.data.owner}'s connections`}
              value={adding}
              onChange={(next) => {
                setAdding(next);
                setScope({});
              }}
              options={[["", "Choose…"], ...available.map((c): [string, string] => [c.name, `${c.name} (${c.env === "prod" ? "production" : c.env})`])]}
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
      </Card>

      <Card className="mb-4">
        <h2 className="mb-3 font-semibold">About this project</h2>
        <ProjectForm existing={project.data} onSaved={() => project.reload()} />
      </Card>
      <Button kind="danger" onClick={remove}>
        Remove project
      </Button>
    </>
  );
}
