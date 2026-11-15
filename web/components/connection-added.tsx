"use client";

import { useState } from "react";

import { CheckList, keepPicked } from "@/components/check-list";
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
  const [chooseSome, setChooseSome] = useState<boolean | null>(null); // null: by whether it has an account
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
  // A shared tool can go everywhere, even with no projects yet; a client's needs two to choose from.
  const offerAll = Boolean(connection && !connection.all_projects && !projects.loading && (connection.shared || theirs.length > 1));
  const everyone = connection?.shared ? "all projects" : `all of ${owner}'s projects`;
  // A browser or files on this computer start in every project; an account starts in none, chosen one by one.
  const account = connection?.holds_account !== false;
  const choosing = chooseSome ?? account;
  const group = (p: { owner: string }) => (connection?.shared ? clientName(p.owner) : undefined);

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
      ) : offerAll && !choosing ? (
        <div className="space-y-2">
          <CheckList
            legend={`Use in ${everyone}`}
            hint={`${connection?.shared ? "New clients and projects get it too." : "Projects added later get it too."}${theirs.length ? " Leave out any that shouldn't have it:" : ""}`}
            items={theirs.map((p) => ({
              key: p.name,
              text: projectName(p.name),
              group: group(p),
              label: (
                <>
                  {projectName(p.name)}
                  {connection?.shared && theirs.length <= 8 ? <span className="text-xs text-muted">{clientName(p.owner)}</span> : null}
                </>
              ),
            }))}
            selected={theirs.filter((p) => !leftOut.includes(p.name)).map((p) => p.name)}
            onChange={(next) => setLeftOut(theirs.filter((p) => !next.includes(p.name)).map((p) => p.name))}
          />
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={theirs.length > 0 && leftOut.length === theirs.length} onClick={useInAll}>
              {leftOut.length ? `Use in ${everyone} except ${leftOut.length}` : `Use in ${everyone}`}
            </Button>
            {theirs.length ? (
              <Button kind="ghost" onClick={() => setChooseSome(true)}>
                Choose projects instead
              </Button>
            ) : null}
          </div>
        </div>
      ) : connection && !projects.loading && !theirs.length ? (
        <NoProjects client={connection.shared ? undefined : connection.owner} />
      ) : eligible.length ? (
        <div className="space-y-2">
          <CheckList
            legend={connection?.projects.length ? "Also use in…" : "Use in…"}
            hint={account ? "This connection has an account behind it. Pick the projects that should be able to use it." : undefined}
            items={eligible.map((p) => ({
              key: p.name,
              text: projectName(p.name),
              group: group(p),
              label: (
                <>
                  {projectName(p.name)}
                  {connection?.shared && eligible.length <= 8 ? <span className="text-xs text-muted">{clientName(p.owner)}</span> : null}
                </>
              ),
              detail:
                p.name in picked && fields.length ? (
                  <ScopeInputs fields={fields} value={picked[p.name] ?? {}} onChange={(scope) => setPicked((all) => ({ ...all, [p.name]: scope }))} connection={connection} />
                ) : undefined,
            }))}
            selected={chosen}
            onChange={(next) => setPicked((all) => keepPicked(all, next, () => ({})))}
          />
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" busy={busy} disabled={!chosen.length} onClick={link}>
              {!chosen.length ? "Choose projects" : chosen.length > 1 ? `Link to ${chosen.length} projects` : "Link to this project"}
            </Button>
            {offerAll && choosing ? (
              <Button kind="ghost" onClick={() => setChooseSome(false)}>
                Use in {everyone} instead
              </Button>
            ) : null}
          </div>
        </div>
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
