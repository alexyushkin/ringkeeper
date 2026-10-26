"use client";

import { useState } from "react";

import { TestButton, type ConnectionData } from "@/components/forms";
import { Alert, Badge, Button, Card, Details, Empty, EnvBadge, Field, LinkButton, Loading, PageHeader, Select, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { sameClient, useScope } from "@/lib/client/scope";
import { SERVICE } from "@/lib/client/words";

export default function ConnectionsPage() {
  const connections = useApi<ConnectionData[]>("connections");
  const scope = useScope();
  // The switcher: a client's connections, or the ones a project uses.
  const shown = (connections.data ?? []).filter(
    (c) => (!scope.client || sameClient(c.owner, scope.client)) && (!scope.project || c.projects.includes(scope.project)),
  );
  return (
    <>
      <PageHeader title="Connections" subtitle="Databases and automation tools your projects use. Secrets are stored encrypted and never shown again." action={<LinkButton href="/connections/new" kind="primary">Add</LinkButton>} />
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      {connections.data && shown.length === 0 ? <Empty>{connections.data.length ? "None for this client or project." : "No connections yet."}</Empty> : null}
      <div className="space-y-3">
        {shown.map((connection) => (
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
      {connection.type === "mcp" ? <McpTools name={connection.name} onChange={onChange} /> : null}
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

type Level = "read" | "approve" | "confirm";

interface McpTool {
  name: string;
  description: string;
  rule: Level;
  rule_from: "you" | "annotations" | "name" | "default";
  default: Level;
  default_from: "annotations" | "name" | "default";
  hints: { read_only: boolean | null; destructive: boolean | null };
}

const RUNS: Record<Level, string> = {
  read: "Runs without asking",
  approve: "Waits for your approval",
  confirm: "Waits; approving needs the name typed",
};
const FROM: Record<McpTool["default_from"], string> = {
  annotations: "from the server's annotations",
  name: "from its name",
  default: "no annotations",
};

/** What the server says about a tool (its MCP annotations), if anything. */
function hintBadge(tool: McpTool) {
  if (tool.hints.read_only) return <Badge tone="good">Server: read-only</Badge>;
  if (tool.hints.destructive) return <Badge tone="bad">Server: destructive</Badge>;
  if (tool.hints.destructive === false) return <Badge>Server: not destructive</Badge>;
  return null;
}

/** An imported MCP server's tools: what a call to each one needs. */
function McpTools({ name, onChange }: { name: string; onChange: () => void }) {
  const [open, setOpen] = useState(false);
  const tools = useApi<{ tools: McpTool[] }>(open ? `connections/${name}/tools` : null);
  const [edited, setEdited] = useState<Record<string, Level | ""> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const listed = tools.data?.tools ?? [];
  const chosen = edited ?? Object.fromEntries(listed.filter((t) => t.rule_from === "you").map((t) => [t.name, t.rule]));

  async function save() {
    setError(null);
    setSaved(false);
    try {
      const rules = Object.fromEntries(Object.entries(chosen).filter(([, level]) => level));
      await api(`connections/${name}/tool-rules`, { method: "PUT", json: { rules } });
      setSaved(true);
      setEdited(null);
      tools.reload();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (!open) {
    return (
      <button type="button" className="text-sm text-accent" onClick={() => setOpen(true)}>
        Tools and approvals
      </button>
    );
  }
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <p className="text-sm text-muted">
        Each tool starts from what its server says about it (its MCP annotations): read-only tools run without asking, destructive ones need the connection's name typed to approve, and the rest wait for your approval. With no annotations, a tool whose name sounds like deleting needs the name typed. Change any of them here.
      </p>
      {tools.loading ? <Loading /> : null}
      {tools.error ? <Alert>{tools.error}</Alert> : null}
      <ul className="space-y-3">
        {listed.map((tool) => (
          <li key={tool.name} className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm">{tool.name}</span>
              {hintBadge(tool)}
            </div>
            {tool.description ? <p className="text-xs text-muted">{tool.description.slice(0, 200)}</p> : null}
            <Select
              label="When the agent calls it"
              value={chosen[tool.name] ?? ""}
              onChange={(level) => setEdited({ ...chosen, [tool.name]: level as Level | "" })}
              options={[
                ["", `${RUNS[tool.default]} (default, ${FROM[tool.default_from]})`],
                ...(Object.keys(RUNS) as Level[]).map((level): [string, string] => [level, RUNS[level]]),
              ]}
            />
          </li>
        ))}
      </ul>
      {saved ? <Alert tone="good">Saved.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex gap-2">
        <Button kind="primary" disabled={!tools.data || edited === null} onClick={save}>
          Save
        </Button>
        <Button kind="ghost" onClick={() => setOpen(false)}>
          Close
        </Button>
      </div>
    </div>
  );
}