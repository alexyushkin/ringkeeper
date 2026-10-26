"use client";

import { useState } from "react";

import { Alert, Button, Card, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

interface Server {
  name: string;
  transport: string;
  preview: string;
  secrets: number;
  problem: string | null;
  imported_as: string | null;
}

interface Found {
  config: string | null;
  servers: Server[];
}

/**
 * The MCP servers in Claude Desktop's settings, each assigned to a project (or
 * skipped). Ringkeeper reads their secrets from the file itself and puts them in
 * its vault; they never come through this page.
 */
export function ClaudeDesktopImport({ projects, onImported, quiet = false }: { projects: string[]; onImported?: () => void; quiet?: boolean }) {
  const found = useApi<Found>(DESKTOP ? "desktop/claude-desktop-servers" : null);
  const [chosen, setChosen] = useState<Record<string, string>>({}); // server -> project ("" = skip)
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const servers = found.data?.servers ?? [];
  if (!DESKTOP || (quiet && servers.length === 0)) return null;
  const picks = Object.entries(chosen).filter(([, project]) => project);

  async function importChosen() {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ imported: Record<string, string> }>("desktop/claude-desktop-servers/import", {
        json: { servers: picks.map(([name, project]) => ({ name, project })) },
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
        <p className="text-sm text-muted">Claude Desktop's settings aren't on this computer.</p>
      ) : servers.length === 0 ? (
        <p className="text-sm text-muted">Claude Desktop has no MCP servers set up.</p>
      ) : (
        <>
          <p className="text-sm text-muted">
            The MCP servers you set up in Claude Desktop. Give each one to a project, or skip it. Their keys move into Ringkeeper's encrypted vault. Every call waits for your approval until you mark a tool as read-only (under Connections).
          </p>
          {projects.length === 0 ? <p className="text-sm">Add a project first, then give servers to it.</p> : null}
          <ul className="space-y-2">
            {servers.map((s) => (
              <li key={s.name} className="space-y-2 rounded-xl border border-line p-3">
                <div>
                  <span className="font-medium">{s.name}</span>
                  {s.secrets ? <span className="ml-2 text-xs text-muted">{s.secrets === 1 ? "1 secret" : `${s.secrets} secrets`}</span> : null}
                  <span className="block font-mono text-xs break-all text-muted">{s.preview}</span>
                </div>
                {s.imported_as ? (
                  <p className="text-sm text-muted">Imported as {s.imported_as}.</p>
                ) : s.problem ? (
                  <p className="text-sm text-muted">Can't import: {s.problem}.</p>
                ) : projects.length ? (
                  <Select
                    label="For project"
                    value={chosen[s.name] ?? ""}
                    onChange={(project) => setChosen((all) => ({ ...all, [s.name]: project }))}
                    options={[["", "Skip"], ...projects.map((p): [string, string] => [p, p])]}
                  />
                ) : null}
              </li>
            ))}
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
