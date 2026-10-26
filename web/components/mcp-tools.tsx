"use client";

import { useState } from "react";

import { Alert, Badge, Button, Loading, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

type Level = "read" | "approve" | "confirm";

interface McpTool {
  name: string;
  description: string;
  rule: Level;
  rule_from: "you" | "annotations" | "name" | "default" | "sql";
  default: Level;
  default_from: "annotations" | "name" | "default" | "sql";
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
  sql: "deleting or changing tables needs the name typed",
};

/** What the server says about a tool (its MCP annotations), if anything. */
function hintBadge(tool: McpTool) {
  if (tool.hints.read_only) return <Badge tone="good">Server: read-only</Badge>;
  if (tool.hints.destructive) return <Badge tone="bad">Server: destructive</Badge>;
  if (tool.hints.destructive === false) return <Badge>Server: not destructive</Badge>;
  return null;
}

/** An imported MCP server's tools: what a call to each one needs. */
export function McpTools({ name, onChange }: { name: string; onChange: () => void }) {
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