"use client";

import { useState } from "react";

import { connectionTitle } from "@/components/connection-form";
import { NoProjects, ScopeInputs, type LibraryConnection } from "@/components/connection-library";
import { TestButton } from "@/components/forms";
import { useProjects } from "@/components/shared-tools";
import { Alert, Button } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientName, projectName } from "@/lib/client/scope";

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * After a connection is created, wherever that was: it worked, test it, use it in
 * other projects too (a client's connection: that client's; a shared tool: any),
 * each narrowed where the service allows, or add another one.
 */
export function ConnectionAdded({ name, onAnother, onDone, onLinked, doneLabel = "Done" }: { name: string; onAnother?: () => void; onDone?: () => void; onLinked?: () => void; doneLabel?: string }) {
  const connections = useApi<LibraryConnection[]>("connections");
  const projects = useProjects();
  const [picked, setPicked] = useState<Record<string, Record<string, string>>>({}); // project -> scope
  const [linked, setLinked] = useState<string[]>([]);
  const [chooseSome, setChooseSome] = useState(false); // all the client's projects, unless not
  const [leftOut, setLeftOut] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connection = (connections.data ?? []).find((c) => c.name === name);
  const fields = connection?.scope_fields ?? [];
  const has = new Set([...(connection?.projects ?? []), ...linked]);
  const theirs = connection ? projects.list.filter((p) => connection.shared || same(p.owner, connection.owner)) : [];
  const eligible = theirs.filter((p) => !has.has(p.name));
  const chosen = Object.keys(picked);
  const owner = connection ? clientName(connection.owner) : "";
  const offerAll = Boolean(connection && !connection.shared && !connection.all_projects && theirs.length > 1);

  async function useInAll() {
    setBusy(true);
    setError(null);
    try {
      await api(`connections/${name}`, { method: "PATCH", json: { all_projects: true, except_projects: leftOut } });
      connections.reload();
      onLinked?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function link() {
    setBusy(true);
    setError(null);
    try {
      for (const [project, scope] of Object.entries(picked)) {
        const narrowed = Object.fromEntries(Object.entries(scope).filter(([, v]) => v.trim()));
        await api(`projects/${project}/connections/${name}`, { method: "PUT", json: { is_default: false, scope: narrowed } });
        setLinked((all) => [...all, project]);
      }
      setPicked({});
      connections.reload();
      onLinked?.(); // the page's own list shows the new links too
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <Alert tone="good">
        Added {connection ? connectionTitle(connection) : name}
        {connection?.projects.length ? `, for ${connection.projects.map(projectName).join(", ")}` : ""}.
        {connection?.config.source === "open" ? " It needs no key." : " Its secrets are stored encrypted and won't be shown again."}
      </Alert>
      <TestButton name={name} />
      {connection?.all_projects ? (
        <p className="text-sm text-muted">{connection.reach}, new ones too. A project&apos;s link can be narrowed under Details.</p>
      ) : connection && !projects.loading && !theirs.length ? (
        <NoProjects client={connection.shared ? undefined : connection.owner} />
      ) : offerAll && !chooseSome ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Use in all of {owner}&apos;s projects</legend>
          <p className="text-xs text-muted">Projects added later get it too. Leave out any that shouldn&apos;t have it:</p>
          {theirs.map((p) => (
            <label key={p.name} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={!leftOut.includes(p.name)}
                onChange={(e) => setLeftOut((all) => (e.target.checked ? all.filter((n) => n !== p.name) : [...all, p.name]))}
                className="size-4 accent-[var(--color-accent)]"
              />
              {projectName(p.name)}
            </label>
          ))}
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={leftOut.length === theirs.length} onClick={useInAll}>
              {leftOut.length ? `Use in all of ${owner}'s projects except ${leftOut.length}` : `Use in all of ${owner}'s projects`}
            </Button>
            <Button kind="ghost" onClick={() => setChooseSome(true)}>
              Choose projects instead
            </Button>
          </div>
        </fieldset>
      ) : eligible.length ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">{connection?.projects.length ? "Also use in…" : "Use in…"}</legend>
          {eligible.map((p) => (
            <div key={p.name} className="space-y-1">
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={p.name in picked}
                  onChange={(e) =>
                    setPicked((all) => {
                      const next = { ...all };
                      if (e.target.checked) next[p.name] = {};
                      else delete next[p.name];
                      return next;
                    })
                  }
                  className="size-4 accent-[var(--color-accent)]"
                />
                {projectName(p.name)}
                {connection?.shared ? <span className="text-xs text-muted">{clientName(p.owner)}</span> : null}
              </label>
              {p.name in picked && fields.length ? (
                <div className="ml-6">
                  <ScopeInputs fields={fields} value={picked[p.name] ?? {}} onChange={(scope) => setPicked((all) => ({ ...all, [p.name]: scope }))} />
                </div>
              ) : null}
            </div>
          ))}
          <Button kind="primary" busy={busy} disabled={!chosen.length} onClick={link}>
            {chosen.length > 1 ? `Link to ${chosen.length} projects` : "Link to this project"}
          </Button>
        </fieldset>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        {onAnother ? (
          <Button onClick={onAnother}>Add another connection</Button>
        ) : null}
        {onDone ? (
          <Button kind="ghost" onClick={onDone}>
            {doneLabel}
          </Button>
        ) : null}
      </div>
    </div>
  );
}
