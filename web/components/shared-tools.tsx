"use client";

import { LibraryCard, type LibraryConnection, type RemoteService } from "@/components/connection-library";
import { Alert, Empty, Loading, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";

/**
 * Two kinds of connection. A client's connection holds that client's account and
 * is linked only to their projects. A shared tool (a browser, files on this
 * computer, a fetcher) holds no client's data: it's yours, and any client's
 * project can use it, or all of them, new ones too.
 */

export type Kind = "client" | "shared";

/** Who owns a shared tool (instances.SHARED_OWNER on the server). */
export const SHARED_OWNER = "you";

export interface ProjectRef {
  name: string;
  owner: string;
}

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export function useProjects() {
  const projects = useApi<ProjectRef[]>("projects");
  return { list: projects.data ?? [], loading: projects.loading, reload: projects.reload };
}

/** "A client's account or your own tool?", defaulting to what the server looks like. */
export function KindChoice({ value, onChange, skip = false }: { value: Kind | "skip"; onChange: (kind: Kind | "skip") => void; skip?: boolean }) {
  const options: [string, string][] = [
    ["client", "A client's account (only that client's projects)"],
    ["shared", "Your own tool (any client's projects)"],
  ];
  return (
    <Select
      label="A client's account or your own tool?"
      value={value}
      onChange={(v) => onChange(v as Kind | "skip")}
      options={skip ? [["skip", "Skip"], ...options] : options}
      hint={value === "shared" ? "It holds no client's data. Anything not marked read-only still waits for your approval." : undefined}
    />
  );
}

/**
 * Projects to link to: a client's connection to projects of one client; a shared
 * tool to any, or to all of them (new ones too).
 */
export function ProjectPicker({
  projects,
  kind,
  value,
  onChange,
  all = false,
  onAll,
  label = "For projects",
}: {
  projects: ProjectRef[];
  kind: Kind;
  value: string[];
  onChange: (next: string[]) => void;
  all?: boolean;
  onAll?: (on: boolean) => void;
  label?: string;
}) {
  const chosenClient = kind === "client" ? projects.find((p) => value.includes(p.name))?.owner : undefined;
  const clients = [...new Set(projects.map((p) => p.owner))].sort((a, b) => a.localeCompare(b));
  const toggle = (name: string, on: boolean) => onChange(on ? [...value, name] : value.filter((v) => v !== name));
  if (!projects.length) return <p className="text-sm text-muted">Add a project first.</p>;
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">{label}</legend>
      {kind === "shared" && onAll ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={all} onChange={(e) => onAll(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
          <span>
            <strong>All projects</strong> <span className="text-muted">(new ones too)</span>
          </span>
        </label>
      ) : null}
      {!all ? (
        <div className="max-h-56 space-y-2 overflow-y-auto rounded-xl border border-line p-2">
          {clients.map((client) => {
            const locked = chosenClient !== undefined && !same(chosenClient, client);
            return (
              <div key={client}>
                <p className="text-xs font-semibold text-muted">{client}</p>
                <div className="flex flex-wrap gap-x-4 gap-y-1">
                  {projects
                    .filter((p) => p.owner === client)
                    .map((p) => (
                      <label key={p.name} className={`flex items-center gap-2 text-sm ${locked ? "opacity-50" : ""}`} title={locked ? "A client's connection is only for one client's projects" : undefined}>
                        <input type="checkbox" disabled={locked} checked={value.includes(p.name)} onChange={(e) => toggle(p.name, e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
                        {p.name}
                      </label>
                    ))}
                </div>
              </div>
            );
          })}
        </div>
      ) : null}
    </fieldset>
  );
}

/** Your shared tools, each linkable to any client's project, or to all of them. */
export function SharedTools() {
  const connections = useApi<LibraryConnection[]>("connections");
  const services = useApi<RemoteService[]>("remote-services");
  const projects = useProjects();
  const tools = (connections.data ?? []).filter((c) => c.shared);
  const reload = () => {
    connections.reload();
    projects.reload();
  };
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        Tools that hold no client&apos;s data, such as a browser or files on this computer. Link one to any client&apos;s projects, or to all of them. Anything not marked read-only waits for your approval.
      </p>
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      {connections.data && !tools.length ? <Empty>No shared tools yet. Add one, or import one from Claude Desktop and choose &quot;Your own tool&quot;.</Empty> : null}
      {tools.map((tool) => (
        <LibraryCard key={tool.name} connection={tool} projects={projects.list.map((p) => p.name)} services={services.data ?? []} onChange={reload} compact />
      ))}
    </div>
  );
}
