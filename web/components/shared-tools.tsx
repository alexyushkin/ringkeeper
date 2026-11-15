"use client";

import { CheckList } from "@/components/check-list";
import { LibraryCard, type LibraryConnection, type RemoteService } from "@/components/connection-library";
import { Alert, Empty, Loading, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";
import { clientName, projectName } from "@/lib/client/scope";

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
  if (!projects.length) return <p className="text-sm text-muted">Add a project first.</p>;
  const sorted = [...projects].sort((a, b) => clientName(a.owner).localeCompare(clientName(b.owner)) || a.name.localeCompare(b.name));
  return (
    <div className="space-y-2">
      {kind === "shared" && onAll ? (
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={all} onChange={(e) => onAll(e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
          <span>
            <strong>All projects</strong> <span className="text-muted">(new ones too)</span>
          </span>
        </label>
      ) : null}
      {!all ? (
        <CheckList
          legend={label}
          items={sorted.map((p) => ({ key: p.name, text: projectName(p.name), group: clientName(p.owner), label: projectName(p.name) }))}
          selected={value}
          onChange={onChange}
          groups="always"
          oneGroup={kind === "client"}
          oneGroupTitle="A client's connection is only for one client's projects"
        />
      ) : null}
    </div>
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
      {connections.data && !tools.length ? <Empty>None yet. Add your own tool (a browser like Playwright, files on this computer), or import one from Claude Desktop and choose &quot;Your own tool&quot;.</Empty> : null}
      {tools.map((tool) => (
        <LibraryCard key={tool.name} connection={tool} projects={projects.list.map((p) => p.name)} services={services.data ?? []} onChange={reload} compact />
      ))}
    </div>
  );
}
