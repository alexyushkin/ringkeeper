"use client";

import { usePrefs } from "@/lib/client/prefs";
import { place, projectName, sameClient, useScope } from "@/lib/client/scope";

/** Above a list the switcher narrows: what's left out isn't gone, and this says so. */
export function ScopeNote() {
  const scope = useScope();
  if (!scope.client && !scope.project) return null;
  return (
    <p className="mb-3 flex flex-wrap items-center gap-2 rounded-xl border border-line bg-surface px-3 py-2 text-sm">
      <span className="text-muted">Showing {place(scope.client, scope.project)} only.</span>
      <button type="button" className="text-accent" onClick={() => scope.set({ client: "", project: "" })}>
        Show everything
      </button>
    </p>
  );
}

/** The top bar's client › project switcher: it filters every list in the app. What you
 * pinned and what you used lately come first. */
export function ScopeSwitcher() {
  const scope = useScope();
  const prefs = usePrefs();
  const rows = scope.projects;
  // Every client, also those with only connections or nothing yet.
  const clients = scope.clients;
  const theirs = rows.filter((p) => !scope.client || sameClient(p.owner, scope.client));
  const select = "min-h-9 rounded-lg border border-line bg-surface px-2 text-sm";
  const current = scope.project ? `project:${scope.project}` : scope.client ? `client:${scope.client}` : "";
  const pinned = current !== "" && prefs.pins.includes(current);
  const named = (kind: string, list: string[]) => list.filter((key) => key.startsWith(`${kind}:`)).map((key) => key.slice(kind.length + 1));
  const firstClients = (list: string[]) => clients.filter((c) => named("client", list).includes(c.slug));
  const firstProjects = (list: string[]) => theirs.filter((p) => named("project", list).includes(p.name));
  const recentOnly = (list: string[]) => list.filter((key) => !prefs.pins.includes(key));

  function choose(next: { client: string; project: string }) {
    scope.set(next);
    const key = next.project ? `project:${next.project}` : next.client ? `client:${next.client}` : "";
    if (key) prefs.touch(key);
  }

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2 text-sm">
      <label className="sr-only" htmlFor="scope-client">
        Client
      </label>
      <select id="scope-client" className={select} value={scope.client} onChange={(event) => choose({ client: event.target.value, project: "" })}>
        <option value="">All clients</option>
        {firstClients(prefs.pins).length ? (
          <optgroup label="Pinned">
            {firstClients(prefs.pins).map((client) => (
              <option key={client.slug} value={client.slug}>
                ★ {client.name}
              </option>
            ))}
          </optgroup>
        ) : null}
        {firstClients(recentOnly(prefs.recents)).length ? (
          <optgroup label="Recent">
            {firstClients(recentOnly(prefs.recents)).map((client) => (
              <option key={client.slug} value={client.slug}>
                {client.name}
              </option>
            ))}
          </optgroup>
        ) : null}
        <optgroup label="All">
          {clients.map((client) => (
            <option key={client.slug} value={client.slug}>
              {client.name}
            </option>
          ))}
        </optgroup>
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
          choose({ client: event.target.value ? owner : scope.client, project: event.target.value });
        }}
      >
        <option value="">{scope.client ? "All their projects" : "All projects"}</option>
        {firstProjects(prefs.pins).length ? (
          <optgroup label="Pinned">
            {firstProjects(prefs.pins).map((p) => (
              <option key={p.name} value={p.name}>
                ★ {projectName(p.name)}
              </option>
            ))}
          </optgroup>
        ) : null}
        {firstProjects(recentOnly(prefs.recents)).length ? (
          <optgroup label="Recent">
            {firstProjects(recentOnly(prefs.recents)).map((p) => (
              <option key={p.name} value={p.name}>
                {projectName(p.name)}
              </option>
            ))}
          </optgroup>
        ) : null}
        <optgroup label="All">
          {theirs.map((p) => (
            <option key={p.name} value={p.name}>
              {projectName(p.name)}
            </option>
          ))}
        </optgroup>
      </select>
      {current ? (
        <button type="button" className="text-base leading-none text-accent" aria-pressed={pinned} aria-label={pinned ? "Unpin" : "Pin this"} title={pinned ? "Unpin" : "Pin this"} onClick={() => prefs.togglePin(current)}>
          {pinned ? "★" : "☆"}
        </button>
      ) : null}
      {scope.client || scope.project ? (
        <button type="button" className="text-xs text-accent" onClick={() => scope.set({ client: "", project: "" })}>
          Show everything
        </button>
      ) : null}
    </div>
  );
}
