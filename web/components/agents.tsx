"use client";

import { useState } from "react";

import Link from "@/components/link";
import { Alert, Badge, Button, CompactRow, ConfirmButton, Empty, Field, Loading, Select, TextArea } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { projectHref } from "@/lib/client/routes";
import { place, useScope } from "@/lib/client/scope";
import { AUTONOMY } from "@/lib/client/words";

/** The pair, in one line, wherever either is empty. */
export const AGENTS_AND_ROUTINES = "An agent is who does the work; a routine is when it runs.";

export interface AgentData {
  name: string;
  instructions: string;
  connections: string[];
  model: string | null;
  effort: string | null;
  autonomy: string | null;
  routines: number;
  project?: string;
  client?: string;
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
      {agents.data && !agents.data.length && !adding ? <Empty>No agents yet. {AGENTS_AND_ROUTINES}</Empty> : null}
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

/** The connections linked to a project, for choosing an agent's. */
function useLinked(project: string): Linked[] | null {
  const detail = useApi<{ instances: { name: string; type: string }[] }>(`projects/${project}`);
  const connections = useApi<{ name: string; shared: boolean }[]>("connections");
  if (!detail.data) return null;
  const shared = new Set((connections.data ?? []).filter((c) => c.shared).map((c) => c.name));
  return detail.data.instances.map((i) => ({ name: i.name, type: i.type, shared: shared.has(i.name) }));
}

/** An agent's form outside its project's page: loads what the project is linked to. */
export function ProjectAgentForm({ project, existing, onSaved, onCancel }: { project: string; existing?: AgentData; onSaved: () => void; onCancel?: () => void }) {
  const linked = useLinked(project);
  if (!linked) return <Loading />;
  return <AgentForm project={project} linked={linked} existing={existing} onSaved={onSaved} onCancel={onCancel} />;
}

/** Which project: the switcher's, or one of the switcher's client's (all, without one). */
export function ProjectPicker({ value, onChange, hint }: { value: string; onChange: (project: string) => void; hint?: string }) {
  const scope = useScope();
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const mine = (projects.data ?? []).filter((p) => !scope.client || p.owner === scope.client);
  return (
    <Select
      label="Project"
      value={value}
      onChange={onChange}
      hint={hint}
      options={[["", mine.length ? "Choose a project…" : "No projects yet"], ...mine.map((p): [string, string] => [p.name, place(p.owner, p.name)])]}
    />
  );
}

/** A new agent from anywhere: the project first, then its form. */
export function NewAgent({ onSaved, onCancel }: { onSaved: () => void; onCancel: () => void }) {
  const scope = useScope();
  const [project, setProject] = useState(scope.project);
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <ProjectPicker value={project} onChange={setProject} hint="An agent belongs to one project and uses some of its connections." />
      {project ? <ProjectAgentForm key={project} project={project} onSaved={onSaved} onCancel={onCancel} /> : null}
      {!project ? (
        <Button kind="ghost" onClick={onCancel}>
          Cancel
        </Button>
      ) : null}
    </div>
  );
}

/** The Agents page: every project's agents, narrowed by the client/project switcher. */
export function AgentDirectory() {
  const scope = useScope();
  const list = useApi<AgentData[]>("agents", scope.query);
  const [adding, setAdding] = useState(false);
  return (
    <div className="space-y-3">
      {adding ? (
        <NewAgent
          onSaved={() => {
            setAdding(false);
            list.reload();
          }}
          onCancel={() => setAdding(false)}
        />
      ) : (
        <Button kind="primary" onClick={() => setAdding(true)}>
          New agent
        </Button>
      )}
      {list.error ? <Alert>{list.error}</Alert> : null}
      {list.data && !list.data.length ? <Empty>No agents yet. {AGENTS_AND_ROUTINES}</Empty> : null}
      <div className="space-y-2">
        {(list.data ?? []).map((agent) => (
          <CompactRow
            key={`${agent.project}/${agent.name}`}
            head={
              <>
                <span className="font-medium">{agent.name}</span>
                <Link href={projectHref(agent.project ?? "")} className="text-xs text-accent">
                  {place(agent.client, agent.project)}
                </Link>
                <span className="text-xs text-muted">{agent.connections.length ? agent.connections.join(", ") : "no connections"}</span>
                {agent.routines ? <Badge>{agent.routines === 1 ? "1 routine" : `${agent.routines} routines`}</Badge> : null}
              </>
            }
          >
            <ProjectAgentForm project={agent.project ?? ""} existing={agent} onSaved={list.reload} />
          </CompactRow>
        ))}
      </div>
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
