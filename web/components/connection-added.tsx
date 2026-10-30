"use client";

import { useState } from "react";

import { ScopeInputs, type LibraryConnection } from "@/components/connection-library";
import { TestButton } from "@/components/forms";
import { useProjects } from "@/components/shared-tools";
import { Alert, Button } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

const same = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/**
 * After a connection is created, wherever that was: it worked, test it, use it in
 * other projects too (a client's connection: that client's; a shared tool: any),
 * each narrowed where the service allows, or add another one.
 */
export function ConnectionAdded({ name, onAnother, onDone, doneLabel = "Done" }: { name: string; onAnother?: () => void; onDone?: () => void; doneLabel?: string }) {
  const connections = useApi<LibraryConnection[]>("connections");
  const projects = useProjects();
  const [picked, setPicked] = useState<Record<string, Record<string, string>>>({}); // project -> scope
  const [linked, setLinked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const connection = (connections.data ?? []).find((c) => c.name === name);
  const fields = connection?.scope_fields ?? [];
  const has = new Set([...(connection?.projects ?? []), ...linked]);
  const eligible = connection ? projects.list.filter((p) => !has.has(p.name) && (connection.shared || same(p.owner, connection.owner))) : [];
  const chosen = Object.keys(picked);

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
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <Alert tone="good">
        Added {name}
        {connection?.projects.length ? `, for ${connection.projects.join(", ")}` : ""}. Its secrets are stored encrypted and won&apos;t be shown again.
      </Alert>
      <TestButton name={name} />
      {connection?.shared && connection.all_projects ? (
        <p className="text-sm text-muted">Every project has it, new ones too.</p>
      ) : eligible.length ? (
        <fieldset className="space-y-2">
          <legend className="text-sm font-medium">Also use in…</legend>
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
                {p.name}
                {connection?.shared ? <span className="text-xs text-muted">{p.owner}</span> : null}
              </label>
              {p.name in picked && fields.length ? (
                <div className="ml-6">
                  <ScopeInputs fields={fields} value={picked[p.name] ?? {}} onChange={(scope) => setPicked((all) => ({ ...all, [p.name]: scope }))} />
                </div>
              ) : null}
            </div>
          ))}
          <Button kind="primary" busy={busy} disabled={!chosen.length} onClick={link}>
            {chosen.length > 1 ? `Use in ${chosen.length} projects` : "Use in this project too"}
          </Button>
        </fieldset>
      ) : null}
      {linked.length ? <p className="text-sm text-muted">Also in {linked.join(", ")}.</p> : null}
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
