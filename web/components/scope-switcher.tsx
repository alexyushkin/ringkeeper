"use client";

import { useApi } from "@/lib/client/api";
import { sameClient, useScope } from "@/lib/client/scope";

interface ProjectRow {
  name: string;
  owner: string;
}

/** The top bar's client › project switcher: it filters every list in the app. */
export function ScopeSwitcher() {
  const scope = useScope();
  const projects = useApi<ProjectRow[]>("projects");
  const rows = projects.data ?? [];
  const clients = [...new Map(rows.map((p) => [p.owner.trim().toLowerCase(), p.owner])).values()].sort((a, b) => a.localeCompare(b));
  const theirs = rows.filter((p) => !scope.client || sameClient(p.owner, scope.client));
  const select = "min-h-9 rounded-lg border border-line bg-surface px-2 text-sm";

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2 text-sm">
      <label className="sr-only" htmlFor="scope-client">
        Client
      </label>
      <select id="scope-client" className={select} value={scope.client} onChange={(event) => scope.set({ client: event.target.value, project: "" })}>
        <option value="">All clients</option>
        {clients.map((client) => (
          <option key={client} value={client}>
            {client}
          </option>
        ))}
      </select>
      <span aria-hidden className="text-muted">
        ›
      </span>
      <label className="sr-only" htmlFor="scope-project">
        Project
      </label>
      <select
        id="scope-project"
        className={select}
        value={scope.project}
        onChange={(event) => {
          const owner = rows.find((p) => p.name === event.target.value)?.owner ?? scope.client;
          scope.set({ client: event.target.value ? owner : scope.client, project: event.target.value });
        }}
      >
        <option value="">{scope.client ? "All their projects" : "All projects"}</option>
        {theirs.map((p) => (
          <option key={p.name} value={p.name}>
            {p.name}
          </option>
        ))}
      </select>
      {scope.client || scope.project ? (
        <button type="button" className="text-xs text-accent" onClick={() => scope.set({ client: "", project: "" })}>
          Show everything
        </button>
      ) : null}
    </div>
  );
}
