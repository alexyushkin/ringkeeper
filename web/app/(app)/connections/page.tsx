"use client";

import { useState } from "react";

import { TestButton, type ConnectionData } from "@/components/forms";
import { Alert, Badge, Button, Card, Details, Empty, EnvBadge, Field, LinkButton, Loading, PageHeader, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { SERVICE } from "@/lib/client/words";

export default function ConnectionsPage() {
  const connections = useApi<ConnectionData[]>("connections");
  return (
    <>
      <PageHeader title="Connections" subtitle="Databases and automation tools your projects use. Secrets are stored encrypted and never shown again." action={<LinkButton href="/connections/new" kind="primary">Add</LinkButton>} />
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      {connections.data?.length === 0 ? <Empty>No connections yet.</Empty> : null}
      <div className="space-y-3">
        {connections.data?.map((connection) => (
          <ConnectionCard key={connection.name} connection={connection} onChange={connections.reload} />
        ))}
      </div>
    </>
  );
}

function ConnectionCard({ connection, onChange }: { connection: ConnectionData; onChange: () => void }) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function replace() {
    setError(null);
    try {
      await api(`connections/${connection.name}/secret`, { method: "PUT", json: { secret } });
      setSecret("");
      setSaved(true);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    if (!window.confirm(`Remove ${connection.name}? Its stored secret stays unused.`)) return;
    try {
      await api(`connections/${connection.name}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{connection.name}</span>
        <EnvBadge env={connection.env} />
        <span className="text-xs text-muted">{SERVICE[connection.type] ?? connection.type}</span>
        {connection.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
        {connection.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
      </div>
      <p className="text-sm text-muted">
        Client {connection.owner}
        {connection.projects.length ? ` · used by ${connection.projects.join(", ")}` : " · not linked to a project yet"}
        {connection.last_test_at ? ` · tested ${timeAgo(connection.last_test_at)}` : ""}
      </p>
      {connection.type === "mcp" && connection.config.preview ? <p className="font-mono text-xs break-all text-muted">{connection.config.preview}</p> : null}
      {connection.last_test_ok === false && connection.last_test_message ? <Alert>{connection.last_test_message}</Alert> : null}
      <TestButton name={connection.name} onDone={() => onChange()} />
      {connection.type === "mcp" ? <McpTools name={connection.name} readOnly={connection.config.read_only_tools ?? []} onChange={onChange} /> : null}
      <Details label="Change">
        <div className="space-y-3">
          {connection.type === "mcp" ? (
            <p className="text-sm text-muted">Its settings came from Claude Desktop. To change them, change them there, remove this connection, and import it again.</p>
          ) : (
            <>
              <p className="text-sm">Secret: {connection.secret_stored ? "saved ✓ (hidden)" : "missing"}</p>
              <Field label="New secret" type="password" value={secret} onChange={setSecret} />
            </>
          )}
          <div className="flex gap-2">
            {connection.type === "mcp" ? null : (
              <Button kind="primary" disabled={!secret} onClick={replace}>
                Replace secret
              </Button>
            )}
            <Button kind="danger" onClick={remove}>
              Remove connection
            </Button>
          </div>
          {saved ? <Alert tone="good">Saved. Test the connection to check it.</Alert> : null}
        </div>
      </Details>
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

interface McpTool {
  name: string;
  description: string;
  read_only: boolean;
}

/** An imported MCP server's tools: which ones run without asking (read-only). */
function McpTools({ name, readOnly, onChange }: { name: string; readOnly: string[]; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const tools = useApi<{ tools: McpTool[] }>(open ? `connections/${name}/tools` : null);
  const [marked, setMarked] = useState<string[]>(readOnly);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    setError(null);
    setSaved(false);
    try {
      await api(`connections/${name}/read-only-tools`, { method: "PUT", json: { tools: marked } });
      setSaved(true);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!open) {
    return (
      <button type="button" className="text-sm text-accent" onClick={() => setOpen(true)}>
        Tools and approvals{readOnly.length ? ` (${readOnly.length} read-only)` : ""}
      </button>
    );
  }
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <p className="text-sm text-muted">
        Every call to this server waits for your approval. Tick the tools that only read (look things up, list, fetch) to let them run without asking. A tool whose name says it deletes always needs the connection's name typed.
      </p>
      {tools.loading ? <Loading /> : null}
      {tools.error ? <Alert>{tools.error}</Alert> : null}
      <ul className="space-y-2">
        {(tools.data?.tools ?? []).map((tool) => (
          <li key={tool.name}>
            <label className="flex items-start gap-3">
              <input
                type="checkbox"
                checked={marked.includes(tool.name)}
                onChange={(e) => setMarked((all) => (e.target.checked ? [...all, tool.name] : all.filter((t) => t !== tool.name)))}
                className="mt-1 size-5 accent-[var(--color-accent)]"
              />
              <span className="min-w-0">
                <span className="block font-mono text-sm">{tool.name}</span>
                {tool.description ? <span className="block text-xs text-muted">{tool.description.slice(0, 200)}</span> : null}
              </span>
            </label>
          </li>
        ))}
      </ul>
      {saved ? <Alert tone="good">Saved.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex gap-2">
        <Button kind="primary" disabled={!tools.data} onClick={save}>
          Save
        </Button>
        <Button kind="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
    </div>
  );
}
