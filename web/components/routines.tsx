"use client";

import Link from "@/components/link";
import { useState } from "react";

import { AGENTS_AND_ROUTINES, ProjectAgentForm, ProjectPicker, type AgentData } from "@/components/agents";
import { Alert, Badge, Button, Empty, Field, Loading, Select, StatusBadge, TextArea, Toggle, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { routineHref } from "@/lib/client/routes";
import { place, useScope } from "@/lib/client/scope";

export interface RoutineData {
  id: string;
  project: string;
  client: string;
  agent: string;
  name: string;
  prompt: string;
  trigger: "schedule" | "event" | "manual";
  when: string;
  schedule: Schedule | null;
  event: EventSpec | null;
  budget_usd: number | null;
  max_steps: number;
  max_minutes: number;
  max_runs_per_day: number;
  notify: "app" | "channels" | "none";
  catch_up: boolean;
  paused: boolean;
  project_paused: boolean;
  next_run_at: string | null;
  hook_path: string | null;
  hook_url: string | null;
  last_run_at: string | null;
  last_status: string | null;
}

interface EventSpec {
  source: string;
  connection?: string;
  base?: string;
  table?: string;
  query?: string;
}

interface Schedule {
  every: "day" | "weekdays" | "week" | "month" | "hours";
  time?: string;
  days?: string[];
  day?: number;
  hours?: number;
  tz?: string;
}

type Template = Partial<RoutineData> & { name: string; prompt: string; trigger: RoutineData["trigger"] };

const DAYS: [string, string][] = [["mon", "Mon"], ["tue", "Tue"], ["wed", "Wed"], ["thu", "Thu"], ["fri", "Fri"], ["sat", "Sat"], ["sun", "Sun"]];
const TEMPLATE_HELP: Record<string, string> = {
  "daily-digest": "Weekday mornings: what changed and what needs you.",
  "lead-follow-up": "When a lead comes in: a follow-up email, as a draft.",
  "weekly-report": "Friday afternoons: the week, for the client.",
  "data-sync-check": "Every morning: do the connected systems agree?",
};

export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/** Routines, one row each: when it runs, its last run, the next one, on or paused,
 * Run now. `project`: only that project's. */
export function RoutineList({ project, client }: { project?: string; client?: string }) {
  const list = useApi<RoutineData[]>("routines", project ? { project } : client ? { client } : undefined);
  if (list.error) return <Alert>{list.error}</Alert>;
  if (!list.data) return null;
  if (!list.data.length) return <Empty>No routines yet. {AGENTS_AND_ROUTINES}</Empty>;
  return (
    <ul className="space-y-2">
      {list.data.map((routine) => (
        <li key={routine.id}>
          <RoutineRow routine={routine} showProject={!project} onChange={list.reload} />
        </li>
      ))}
    </ul>
  );
}

function RoutineRow({ routine, showProject, onChange }: { routine: RoutineData; showProject: boolean; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function act(path: string, json: unknown) {
    setError(null);
    try {
      await api(`routines/${routine.id}/${path}`, { json });
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Link href={routineHref(routine.id)} className="font-medium text-accent">
          {routine.name}
        </Link>
        {showProject ? <span className="text-xs text-muted">{place(routine.client, routine.project)}</span> : null}
        <span className="text-xs text-muted">· {routine.agent}</span>
        {routine.paused ? <Badge>paused</Badge> : routine.project_paused ? <Badge tone="warn">project paused</Badge> : <Badge tone="good">on</Badge>}
        <span className="ml-auto text-xs text-muted">{routine.when}</span>
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted">
        <span>
          Last run: {routine.last_run_at ? timeAgo(routine.last_run_at) : "never"}
          {routine.last_status ? ` (${routine.last_status})` : ""}
        </span>
        {!routine.paused && routine.next_run_at ? <span>Next: {new Date(routine.next_run_at).toLocaleString()}</span> : null}
        <span className="ml-auto flex gap-2">
          <Button kind="ghost" onClick={() => act("run", { test: routine.paused })}>
            {routine.paused ? "Test run" : "Run now"}
          </Button>
          <Button kind="ghost" onClick={() => act("paused", { paused: !routine.paused })}>
            {routine.paused ? "Turn on" : "Pause"}
          </Button>
        </span>
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** A new routine: start from a template (or blank), then the form. */
export function NewRoutine({ project, agents, onCreated }: { project: string; agents: AgentData[]; onCreated: (id: string) => void }) {
  const templates = useApi<Record<string, Template>>("routine-templates");
  const [start, setStart] = useState<Template | null>(null);
  if (!agents.length) return <p className="text-sm text-muted">Add an agent first: a routine runs one. {AGENTS_AND_ROUTINES}</p>;
  if (!start) {
    return (
      <div className="space-y-2">
        <p className="text-sm font-medium">Start from</p>
        <div className="grid gap-2 sm:grid-cols-2">
          {Object.entries(templates.data ?? {}).map(([key, template]) => (
            <button key={key} type="button" onClick={() => setStart(template)} className="rounded-xl border border-line bg-surface p-3 text-left hover:border-accent">
              <span className="block font-medium">{template.name}</span>
              <span className="mt-1 block text-sm text-muted">{TEMPLATE_HELP[key] ?? ""}</span>
            </button>
          ))}
          <button type="button" onClick={() => setStart({ name: "", prompt: "", trigger: "manual" })} className="rounded-xl border border-dashed border-line p-3 text-left hover:border-accent">
            <span className="block font-medium">Blank</span>
            <span className="mt-1 block text-sm text-muted">Your own prompt and trigger.</span>
          </button>
        </div>
      </div>
    );
  }
  return <RoutineForm project={project} agents={agents} start={start} onSaved={onCreated} onCancel={() => setStart(null)} />;
}

/** New routine from the Routines page: the project, then its agent (making one there
 * if it has none), then the routine. */
export function NewRoutineAnywhere({ onCreated, onCancel }: { onCreated: (id: string) => void; onCancel: () => void }) {
  const scope = useScope();
  const [project, setProject] = useState(scope.project);
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <ProjectPicker value={project} onChange={setProject} hint="A routine runs one of the project's agents." />
      {project ? <RoutineFor key={project} project={project} onCreated={onCreated} /> : null}
      <Button kind="ghost" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}

function RoutineFor({ project, onCreated }: { project: string; onCreated: (id: string) => void }) {
  const agents = useApi<AgentData[]>(`projects/${project}/agents`);
  const [making, setMaking] = useState(false);
  if (agents.error) return <Alert>{agents.error}</Alert>;
  if (!agents.data) return <Loading />;
  if (agents.data.length) return <NewRoutine project={project} agents={agents.data} onCreated={onCreated} />;
  if (making) {
    return (
      <div className="space-y-2">
        <p className="text-sm font-medium">New agent for {project}</p>
        <ProjectAgentForm
          project={project}
          onSaved={() => {
            setMaking(false);
            agents.reload();
          }}
          onCancel={() => setMaking(false)}
        />
      </div>
    );
  }
  return (
    <div className="space-y-2">
      <p className="text-sm text-muted">
        {project} has no agents yet. {AGENTS_AND_ROUTINES}
      </p>
      <Button kind="primary" onClick={() => setMaking(true)}>
        Create an agent
      </Button>
    </div>
  );
}

/** A routine's settings: agent, prompt, when it runs, its limits, how you hear of it. */
export function RoutineForm({ project, agents, start, existing, onSaved, onCancel }: { project: string; agents: AgentData[]; start?: Template; existing?: RoutineData; onSaved: (id: string) => void; onCancel?: () => void }) {
  const base = existing ?? start;
  const [name, setName] = useState(base?.name ?? "");
  const [agent, setAgent] = useState(existing?.agent ?? agents[0]?.name ?? "");
  const [prompt, setPrompt] = useState(base?.prompt ?? "");
  const [trigger, setTrigger] = useState<RoutineData["trigger"]>(base?.trigger ?? "manual");
  const [schedule, setSchedule] = useState<Schedule>(base?.schedule ?? { every: "weekdays", time: "08:00" });
  const [source, setSource] = useState(base?.event?.source ?? "webhook");
  const [watch, setWatch] = useState<EventSpec>(base?.event ?? { source: "webhook" });
  const detail = useApi<{ instances: { name: string; type: string }[] }>(`projects/${project}`);
  const ofType = (type: string) => (detail.data?.instances ?? []).filter((i) => i.type === type).map((i): [string, string] => [i.name, i.name]);
  const [steps, setSteps] = useState(String(existing?.max_steps ?? 40));
  const [minutes, setMinutes] = useState(String(existing?.max_minutes ?? 15));
  const [perDay, setPerDay] = useState(String(existing?.max_runs_per_day ?? 10));
  const [budget, setBudget] = useState(existing?.budget_usd ? String(existing.budget_usd) : "");
  const [notify, setNotify] = useState<RoutineData["notify"]>(existing?.notify ?? "app");
  const [catchUp, setCatchUp] = useState(existing?.catch_up ?? true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    const json = {
      name: name.trim(), agent, prompt, trigger,
      schedule: trigger === "schedule" ? schedule : null,
      event: trigger === "event" ? { ...watch, source } : null,
      max_steps: Number(steps), max_minutes: Number(minutes), max_runs_per_day: Number(perDay),
      budget_usd: budget.trim() ? Number(budget) : null, notify, catch_up: catchUp, tz: browserZone(),
    };
    try {
      const saved = await (existing ? api<RoutineData>(`routines/${existing.id}`, { method: "PUT", json }) : api<RoutineData>(`projects/${project}/routines`, { json }));
      onSaved(saved.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const set = (changes: Partial<Schedule>) => setSchedule((now) => ({ ...now, ...changes }));

  return (
    <div className="space-y-3">
      <Field label="Name" value={name} onChange={setName} placeholder="Morning digest" required />
      <Select label="Agent" value={agent} onChange={setAgent} options={agents.map((a): [string, string] => [a.name, a.name])} hint="It works with its own connections and instructions." />
      <TextArea label="What it should do" value={prompt} onChange={setPrompt} rows={4} hint="Fill-ins: {{today}}, {{now}}, {{project}}, and for events {{event}} or one field, like {{event.email}}." />
      <Select
        label="When it runs"
        value={trigger}
        onChange={(v) => setTrigger(v as RoutineData["trigger"])}
        options={[
          ["schedule", "On a schedule"],
          ["event", "When something happens"],
          ["manual", "Only when I run it"],
        ]}
      />
      {trigger === "schedule" ? (
        <div className="space-y-2 rounded-xl bg-sunken p-3">
          <Select
            label="Every"
            value={schedule.every}
            onChange={(v) => set({ every: v as Schedule["every"] })}
            options={[
              ["day", "Day"],
              ["weekdays", "Weekday (Mon–Fri)"],
              ["week", "Week, on chosen days"],
              ["month", "Month, on a day"],
              ["hours", "Few hours"],
            ]}
          />
          {schedule.every === "hours" ? (
            <Select label="How often" value={String(schedule.hours ?? 4)} onChange={(v) => set({ hours: Number(v) })} options={[1, 2, 3, 4, 6, 8, 12].map((h): [string, string] => [String(h), h === 1 ? "Every hour" : `Every ${h} hours`])} />
          ) : (
            <Field label="At" type="time" value={schedule.time ?? "08:00"} onChange={(v) => set({ time: v })} hint={`Your time (${browserZone()}).`} />
          )}
          {schedule.every === "week" ? (
            <fieldset className="flex flex-wrap gap-3">
              <legend className="mb-1 text-sm font-medium">On</legend>
              {DAYS.map(([key, label]) => (
                <label key={key} className="flex items-center gap-1 text-sm">
                  <input type="checkbox" checked={(schedule.days ?? []).includes(key)} onChange={(e) => set({ days: e.target.checked ? [...(schedule.days ?? []), key] : (schedule.days ?? []).filter((d) => d !== key) })} className="size-4 accent-[var(--color-accent)]" />
                  {label}
                </label>
              ))}
            </fieldset>
          ) : null}
          {schedule.every === "month" ? <Field label="Day of the month" type="number" value={String(schedule.day ?? 1)} onChange={(v) => set({ day: Number(v) })} hint="29–31: the month's last day when it's shorter." /> : null}
          <Toggle label="Catch up after sleep" checked={catchUp} onChange={setCatchUp} hint="If the computer was asleep, run once when it wakes (not once per missed time), and say so." />
        </div>
      ) : null}
      {trigger === "event" ? (
        <div className="space-y-2 rounded-xl bg-sunken p-3">
          <Select
            label="What happens"
            value={source}
            onChange={setSource}
            options={[
              ["webhook", "Its webhook is called (n8n or anything on this computer)"],
              ["folder", "A file is added to the project's folder"],
              ["airtable", "A record is added to an Airtable table"],
              ["gmail", "A matching email arrives (Google account)"],
            ]}
          />
          {source === "airtable" ? (
            <>
              <Select label="Airtable connection" value={watch.connection ?? ""} onChange={(v) => setWatch((w) => ({ ...w, connection: v }))} options={[["", "Choose…"], ...ofType("airtable")]} />
              <Field label="Base ID" value={watch.base ?? ""} onChange={(v) => setWatch((w) => ({ ...w, base: v.trim() }))} placeholder="app…" />
              <Field label="Table" value={watch.table ?? ""} onChange={(v) => setWatch((w) => ({ ...w, table: v }))} placeholder="Leads" />
            </>
          ) : null}
          {source === "gmail" ? (
            <>
              <Select label="Google account" value={watch.connection ?? ""} onChange={(v) => setWatch((w) => ({ ...w, connection: v }))} options={[["", "Choose…"], ...ofType("google")]} />
              <Field label="Which emails (Gmail search)" value={watch.query ?? ""} onChange={(v) => setWatch((w) => ({ ...w, query: v }))} placeholder="from:@acme.example subject:quote" />
            </>
          ) : null}
          <p className="text-xs text-muted">
            {source === "webhook"
              ? "The address appears once it's saved. It answers only on this computer; outside webhooks come with the cloud hub."
              : source === "folder"
                ? "Files already there don't count; each new file starts one run, with {{event.file}}."
                : "Checked every 5 minutes, reading only. What's there now doesn't count; each new one starts one run, with its fields as {{event...}}."}
          </p>
        </div>
      ) : null}
      <div className="grid gap-3 sm:grid-cols-4">
        <Field label="Steps per run" type="number" value={steps} onChange={setSteps} />
        <Field label="Minutes per run" type="number" value={minutes} onChange={setMinutes} />
        <Field label="Runs a day, at most" type="number" value={perDay} onChange={setPerDay} />
        <Field label="$ cap per run" type="number" value={budget} onChange={setBudget} placeholder="default" hint="API key mode only." />
      </div>
      <Select
        label="When a run finishes"
        value={notify}
        onChange={(v) => setNotify(v as RoutineData["notify"])}
        options={[
          ["app", "Show it in the run history"],
          ["channels", "Also send a summary to my notification channels"],
          ["none", "Nothing (the history still has it)"],
        ]}
      />
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!name.trim() || !prompt.trim() || !agent} onClick={save}>
          {existing ? "Save" : "Create (paused)"}
        </Button>
        {onCancel ? (
          <Button kind="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      {!existing ? <p className="text-xs text-muted">New routines start paused: try a test run, then turn it on.</p> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

export interface Run {
  id: string;
  trigger: string;
  created_at: string;
  missed: number;
  skipped: string | null;
  task_id: string | null;
  status: string | null;
  summary: string | null;
  cost_usd: number | null;
  steps: number | null;
  error: string | null;
}

export const TRIGGER: Record<string, string> = {
  schedule: "On schedule",
  catch_up: "Catch-up after sleep",
  event: "Event",
  manual: "Run now",
  test: "Test run",
};

export function RunStatus({ run }: { run: Run }) {
  if (run.skipped) return <Badge tone="warn">skipped</Badge>;
  return run.status ? <StatusBadge status={run.status} /> : null;
}
