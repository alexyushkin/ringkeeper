"use client";

import { useState } from "react";

import { Alert, Badge, Button, CompactRow, ConfirmButton, Empty, Field, Select, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { AUTONOMY } from "@/lib/client/words";

export interface AgentData {
  name: string;
  instructions: string;
  connections: string[];
  model: string | null;
  effort: string | null;
  autonomy: string | null;
  routines: number;
}

/** A connection the project is linked to: an agent can be given some of them. */
export interface Linked {
  name: string;
  type: string;
  shared?: boolean;
}

const EFFORTS: [string, string][] = [
  ["", "The project's default"],
  ["low", "Low: quick and cheap"],
  ["medium", "Medium"],
  ["high", "High: thorough"],
];

/** Project › Agents: named helpers with their own instructions, some of the project's
 * connections (shared tools only when ticked), model, effort and autonomy. Routines
 * run them; a conversation can ask one by name. */
export function Agents({ project, linked }: { project: string; linked: Linked[] }) {
  const agents = useApi<AgentData[]>(`projects/${project}/agents`);
  const [adding, setAdding] = useState(false);
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        An agent works only with the connections you give it, follows its instructions every time, and can run on a routine. In a conversation, ask it by name: “ask the follow-up agent to…”.
      </p>
      {agents.error ? <Alert>{agents.error}</Alert> : null}
      {agents.data && !agents.data.length && !adding ? <Empty>No agents yet.</Empty> : null}
      <div className="space-y-2">
        {(agents.data ?? []).map((agent) => (
          <CompactRow
            key={agent.name}
            head={
              <>
                <span className="font-medium">{agent.name}</span>
                <span className="text-xs text-muted">{agent.connections.length ? agent.connections.join(", ") : "no connections"}</span>
                {agent.routines ? <Badge>{agent.routines === 1 ? "1 routine" : `${agent.routines} routines`}</Badge> : null}
              </>
            }
          >
            <AgentForm project={project} linked={linked} existing={agent} onSaved={agents.reload} />
          </CompactRow>
        ))}
      </div>
      {adding ? (
        <div className="rounded-xl border border-line p-3">
          <AgentForm
            project={project}
            linked={linked}
            onSaved={() => {
              setAdding(false);
              agents.reload();
            }}
            onCancel={() => setAdding(false)}
          />
        </div>
      ) : (
        <Button kind="primary" onClick={() => setAdding(true)}>
          Add an agent
        </Button>
      )}
    </div>
  );
}

function AgentForm({ project, linked, existing, onSaved, onCancel }: { project: string; linked: Linked[]; existing?: AgentData; onSaved: () => void; onCancel?: () => void }) {
  const [name, setName] = useState(existing?.name ?? "");
  const [instructions, setInstructions] = useState(existing?.instructions ?? "");
  const [chosen, setChosen] = useState<string[]>(existing?.connections ?? []);
  const [model, setModel] = useState(existing?.model ?? "");
  const [effort, setEffort] = useState(existing?.effort ?? "");
  const [autonomy, setAutonomy] = useState(existing?.autonomy ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    const json = { name, instructions, connections: chosen, model: model.trim() || null, effort: effort || null, autonomy: autonomy || null };
    try {
      await (existing ? api(`projects/${project}/agents/${existing.name}`, { method: "PUT", json }) : api(`projects/${project}/agents`, { json }));
      setSaved(Boolean(existing));
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!existing) return;
    await api(`projects/${project}/agents/${existing.name}`, { method: "DELETE" });
    onSaved();
  }
  const toggle = (connection: string, on: boolean) => setChosen((now) => (on ? [...now, connection] : now.filter((c) => c !== connection)));

  return (
    <div className="space-y-3">
      <Field label="Name" value={name} onChange={setName} placeholder="follow-up" hint="Short, like follow-up or weekly-report." required />
      <TextArea label="Instructions" value={instructions} onChange={setInstructions} rows={5} hint="What it always does and how: tone, what to check, what never to touch. Every task it runs starts with these." />
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">Connections it may use</legend>
        {linked.length ? (
          linked.map((connection) => (
            <label key={connection.name} className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={chosen.includes(connection.name)} onChange={(e) => toggle(connection.name, e.target.checked)} className="size-4 accent-[var(--color-accent)]" />
              <span>{connection.name}</span>
              <span className="text-xs text-muted">
                {connection.type}
                {connection.shared ? " · shared tool" : ""}
              </span>
            </label>
          ))
        ) : (
          <p className="text-sm text-muted">Link connections to the project first; then give some to the agent.</p>
        )}
        <p className="text-xs text-muted">Only these: the gateway refuses the project&apos;s other connections to this agent. Shared tools only when ticked.</p>
      </fieldset>
      <div className="grid gap-3 sm:grid-cols-3">
        <Field label="Model (optional)" value={model} onChange={setModel} placeholder="The project's default" />
        <Select label="Effort" value={effort} onChange={setEffort} options={EFFORTS} />
        <Select label="How independently it works" value={autonomy} onChange={setAutonomy} options={[["", "The project's setting"], ...Object.entries(AUTONOMY).map(([key, [label]]): [string, string] => [key, label])]} />
      </div>
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!name.trim()} onClick={save}>
          {existing ? "Save" : "Add agent"}
        </Button>
        {onCancel ? (
          <Button kind="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
        {existing ? (
          <ConfirmButton kind="secondary" question={existing.routines ? `Remove ${existing.name}? Its ${existing.routines} routine(s) go with it.` : `Remove ${existing.name}?`} confirm={`Remove ${existing.name}`} onConfirm={remove}>
            Remove
          </ConfirmButton>
        ) : null}
      </div>
      {saved ? <Alert tone="good">Saved.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
