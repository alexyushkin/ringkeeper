"use client";

import { useState } from "react";

import { KindChoice, ProjectPicker, useProjects, type Kind } from "@/components/shared-tools";
import { Alert, Button, Card } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

interface Server {
  name: string;
  key: string;
  transport: string;
  preview: string;
  secrets: number;
  problem: string | null;
  imported_as: string | null;
  suggested: Kind; // a client's account if it needs a key, token, login or address
}

interface Found {
  config: string | null;
  servers: Server[];
}

interface Choice {
  kind: Kind | "skip";
  projects: string[];
  all: boolean;
}

/**
 * The MCP servers in Claude Desktop's settings. Each one is a client's account
 * (linked to projects of that client) or your own tool (linked to any client's
 * projects, or all of them), or skipped. Ringkeeper reads their secrets from the
 * file itself and puts them in its vault; they never come through this page.
 */
export function ClaudeDesktopImport({ onImported, quiet = false }: { onImported?: () => void; quiet?: boolean }) {
  const found = useApi<Found>(DESKTOP ? "desktop/claude-desktop-servers" : null);
  const projects = useProjects();
  const [chosen, setChosen] = useState<Record<string, Choice>>({});
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const servers = found.data?.servers ?? [];
  if (!DESKTOP || (quiet && servers.length === 0)) return null;
  const choiceFor = (s: Server): Choice => chosen[s.key] ?? { kind: s.suggested, projects: [], all: false };
  const set = (s: Server, next: Partial<Choice>) => setChosen((all) => ({ ...all, [s.key]: { ...choiceFor(s), ...next } }));
  const picks = servers
    .filter((s) => !s.imported_as && !s.problem)
    .map((s) => [s, choiceFor(s)] as const)
    .filter(([, c]) => c.kind !== "skip" && (c.projects.length || (c.kind === "shared" && c.all)));

  async function importChosen() {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ imported: Record<string, string> }>("desktop/claude-desktop-servers/import", {
        json: {
          servers: picks.map(([s, c]) => ({ name: s.key, projects: c.kind === "shared" && c.all ? [] : c.projects, shared: c.kind === "shared", all_projects: c.kind === "shared" && c.all })),
        },
      });
      setDone(`Imported ${Object.values(result.imported).join(", ")}.`);
      setChosen({});
      found.reload();
      onImported?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-3">
      <h2 className="text-lg font-semibold">From Claude Desktop</h2>
      {found.data && !found.data.config ? (
        <p className="text-sm text-muted">Claude Desktop&apos;s settings aren&apos;t on this computer.</p>
      ) : servers.length === 0 ? (
        <p className="text-sm text-muted">Claude Desktop has no MCP servers set up.</p>
      ) : (
        <>
          <p className="text-sm text-muted">
            The MCP servers you set up in Claude Desktop. A client&apos;s account goes to that client&apos;s projects; your own tool (a browser, files, a fetcher) can go to any client&apos;s projects, or all of them. Their keys move into Ringkeeper&apos;s encrypted vault. Tools the server marks as read-only run without asking; the rest wait for your approval.
          </p>
          <ul className="space-y-2">
            {servers.map((s) => {
              const c = choiceFor(s);
              return (
                <li key={s.key} className="space-y-2 rounded-xl border border-line p-3">
                  <div>
                    <span className="font-medium">{s.name}</span>
                    {s.secrets ? <span className="ml-2 text-xs text-muted">{s.secrets === 1 ? "1 secret" : `${s.secrets} secrets`}</span> : null}
                    <span className="block font-mono text-xs break-all text-muted">{s.preview}</span>
                  </div>
                  {s.imported_as ? (
                    <p className="text-sm text-muted">Imported as {s.imported_as}.</p>
                  ) : s.problem ? (
                    <p className="text-sm text-muted">Can&apos;t import: {s.problem}.</p>
                  ) : (
                    <>
                      <KindChoice skip value={c.kind} onChange={(kind) => set(s, { kind, projects: [], all: false })} />
                      {c.kind !== "skip" ? <ProjectPicker projects={projects.list} kind={c.kind} value={c.projects} onChange={(next) => set(s, { projects: next })} all={c.all} onAll={(all) => set(s, { all })} /> : null}
                    </>
                  )}
                </li>
              );
            })}
          </ul>
          {done ? <Alert tone="good">{done}</Alert> : null}
          {error ? <Alert>{error}</Alert> : null}
          {picks.length ? (
            <Button kind="primary" busy={busy} onClick={importChosen}>
              {picks.length === 1 ? "Import 1 server" : `Import ${picks.length} servers`}
            </Button>
          ) : null}
        </>
      )}
    </Card>
  );
}
