"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ProjectForm, type ConnectionData, type ProjectData } from "@/components/forms";
import { Alert, Button, Card, EnvBadge, Loading, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { SERVICE } from "@/lib/client/words";

interface Bound {
  name: string;
  type: string;
  env: string;
  is_default: boolean;
}

interface Detail extends ProjectData {
  instances: Bound[];
}

export function ProjectView({ name }: { name: string }) {
  const router = useRouter();
  const project = useApi<Detail>(`projects/${name}`);
  const connections = useApi<ConnectionData[]>("connections");
  const [adding, setAdding] = useState("");
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
  const available = (connections.data ?? []).filter((c) => !bound.has(c.name));

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-4 text-xl font-semibold">{name}</h1>

      <Card className="mb-4 space-y-3">
        <h2 className="font-semibold">Connections</h2>
        <p className="text-sm text-muted">
          The services this project may use. A default is used when a request doesn't name one; production can never be the default, so live data is always named on purpose.
        </p>
        {project.data.instances.map((instance) => (
          <div key={instance.name} className="flex flex-wrap items-center gap-2 rounded-xl border border-line p-3">
            <span className="font-medium">{instance.name}</span>
            <EnvBadge env={instance.env} />
            <span className="text-xs text-muted">{SERVICE[instance.type] ?? instance.type}</span>
            <div className="ml-auto flex items-center gap-3">
              {instance.env !== "prod" ? (
                <Toggle
                  label="Default"
                  checked={instance.is_default}
                  onChange={(on) => run(() => api(`projects/${name}/connections/${instance.name}`, { method: "PUT", json: { is_default: on } }))}
                />
              ) : null}
              <Button kind="ghost" onClick={() => run(() => api(`projects/${name}/connections/${instance.name}`, { method: "DELETE" }))}>
                Remove
              </Button>
            </div>
          </div>
        ))}
        {available.length ? (
          <div className="flex items-end gap-2">
            <div className="flex-1">
              <Select label="Add a connection" value={adding} onChange={setAdding} options={[["", "Choose…"], ...available.map((c): [string, string] => [c.name, `${c.name} (${c.env === "prod" ? "production" : c.env})`])]} />
            </div>
            <Button
              kind="primary"
              disabled={!adding}
              onClick={() =>
                run(async () => {
                  await api(`projects/${name}/connections/${adding}`, { method: "PUT", json: { is_default: false } });
                  setAdding("");
                })
              }
            >
              Add
            </Button>
          </div>
        ) : (
          <p className="text-sm text-muted">
            <Link href="/connections/new" className="text-accent">
              Add a connection
            </Link>{" "}
            first, then link it here.
          </p>
        )}
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
