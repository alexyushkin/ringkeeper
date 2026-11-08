"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Code, Details, Field, Loading, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface Starter {
  key: string;
  name: string;
  description: string;
  needs: string[];
  routines: { name: string; trigger: string }[];
}

interface RecipeRoutine {
  name: string;
  prompt: string;
  trigger: string;
  schedule?: Record<string, unknown>;
  event?: { source: string; watch?: string };
}

interface Recipe {
  ringkeeper_recipe: number;
  name: string;
  description?: string;
  agent: { name: string; instructions?: string; needs?: string[] };
  routines: RecipeRoutine[];
}

interface Needs {
  required: string[];
  connections: Record<string, string[]>;
  events: string[][];
}

const KIND: Record<string, string> = {
  n8n: "n8n", github: "GitHub", vercel: "Vercel", airtable: "Airtable", google: "Google account", twilio: "Twilio",
  smtp: "SMTP", mailbox: "mailbox", resend: "Resend", supabase: "Supabase", neon: "Neon", http: "HTTP API",
};
const FIELD: Record<string, [string, string]> = {
  base: ["Airtable base ID", "appXXXXXXXXXXXXXX"],
  table: ["Table", "Leads"],
  project: ["Vercel project", "my-site"],
  repo: ["Repository", "owner/name"],
  url: ["Website address", "https://example.com"],
};
const WHEN: Record<string, string> = { schedule: "on a schedule", event: "when something happens", manual: "when you run it" };

/** Recipes: an agent with its routines, from the starters, a file or an https link. Shown
 * first; added to the project with its routines paused. */
export function Recipes({ project, onAdded }: { project: string; onAdded: (routine: string | null) => void }) {
  const starters = useApi<Starter[]>("recipes/starters");
  const [recipe, setRecipe] = useState<Recipe | null>(null);
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function read(body: { text?: string; url?: string }) {
    setError(null);
    setBusy(true);
    try {
      setRecipe(await api<Recipe>("recipes/read", { json: body }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function starter(key: string) {
    setError(null);
    try {
      setRecipe(await api<Recipe>(`recipes/starters/${key}`));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (recipe) return <AddRecipe project={project} recipe={recipe} onAdded={onAdded} onCancel={() => setRecipe(null)} />;
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">Or add a recipe: an agent with its routines</p>
      {starters.loading && !starters.data ? <Loading /> : null}
      <div className="grid gap-2 sm:grid-cols-2">
        {(starters.data ?? []).map((s) => (
          <button key={s.key} type="button" onClick={() => void starter(s.key)} className="rounded-xl border border-line bg-surface p-3 text-left hover:border-accent">
            <span className="block font-medium">{s.name}</span>
            <span className="mt-1 block text-sm text-muted">{s.description}</span>
          </button>
        ))}
      </div>
      <Details label="Import a recipe from a file or a link">
        <div className="space-y-3">
          <label className="block text-sm">
            <span className="mb-1 block font-medium">From a file</span>
            <input
              type="file"
              accept=".json,application/json"
              onChange={async (e) => {
                const file = e.target.files?.[0];
                if (file) await read({ text: await file.text() });
              }}
              className="text-sm"
            />
          </label>
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-64 flex-1">
              <Field label="From a link" value={link} onChange={setLink} placeholder="https://github.com/…/recipe.json" />
            </div>
            <Button busy={busy} disabled={!link.trim().startsWith("https://")} onClick={() => void read({ url: link.trim() })}>
              Read it
            </Button>
          </div>
          <p className="text-xs text-muted">A recipe is only settings and instructions: it can&apos;t run code, and has no keys. You see everything in it before it&apos;s added.</p>
        </div>
      </Details>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

function useNeeds(project: string, recipe: Recipe): { data: Needs | null; error: string | null } {
  const [data, setData] = useState<Needs | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api<Needs>(`projects/${project}/recipes/needs`, { json: { recipe } })
      .then((found) => live && setData(found))
      .catch((err: unknown) => live && setError(err instanceof Error ? err.message : String(err)));
    return () => {
      live = false;
    };
  }, [project, recipe]);
  return { data, error };
}

function AddRecipe({ project, recipe, onAdded, onCancel }: { project: string; recipe: Recipe; onAdded: (routine: string | null) => void; onCancel: () => void }) {
  const needs = useNeeds(project, recipe);
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [events, setEvents] = useState<Record<number, Record<string, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const kinds = needs.data?.connections ?? {};
  const pick = (kind: string) => chosen[kind] ?? kinds[kind]?.[0] ?? "";

  async function add() {
    setBusy(true);
    setError(null);
    try {
      const connections = Object.fromEntries(Object.keys(kinds).map((k) => [k, pick(k)]).filter(([, v]) => v));
      const filled = recipe.routines.map((_, i) => events[i] ?? {});
      const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
      const made = await api<{ routines: string[] }>(`projects/${project}/recipes`, { json: { recipe, connections, events: filled, tz } });
      onAdded(made.routines[0] ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      <div>
        <h3 className="font-medium">{recipe.name}</h3>
        {recipe.description ? <p className="text-sm text-muted">{recipe.description}</p> : null}
      </div>
      <Details label={`The agent's instructions (${recipe.agent.name})`}>
        <Code>{recipe.agent.instructions || "(none)"}</Code>
      </Details>
      <div className="space-y-1">
        <p className="text-sm font-medium">{recipe.routines.length === 1 ? "Its routine, added paused" : `Its ${recipe.routines.length} routines, added paused`}</p>
        <ul className="space-y-2">
          {recipe.routines.map((r, i) => (
            <li key={`${r.name}-${i}`} className="rounded-xl border border-line p-2 text-sm">
              <span className="font-medium">{r.name}</span> <span className="text-muted">· runs {WHEN[r.trigger] ?? r.trigger}{r.event ? ` (${r.event.source}${r.event.watch ? `, ${r.event.watch}` : ""})` : ""}</span>
              <Details label="Its prompt">
                <Code>{r.prompt}</Code>
              </Details>
              {(needs.data?.events[i] ?? [])
                .filter((f) => f !== "connection")
                .map((f) => (
                  <Field
                    key={f}
                    label={FIELD[f]?.[0] ?? f}
                    placeholder={FIELD[f]?.[1]}
                    value={events[i]?.[f] ?? ""}
                    onChange={(value) => setEvents((all) => ({ ...all, [i]: { ...(all[i] ?? {}), [f]: value } }))}
                  />
                ))}
            </li>
          ))}
        </ul>
      </div>
      {Object.entries(kinds).map(([kind, names]) =>
        names.length ? (
          <Select key={kind} label={`Its ${KIND[kind] ?? kind} connection`} value={pick(kind)} onChange={(v) => setChosen((all) => ({ ...all, [kind]: v }))} options={names.map((n): [string, string] => [n, n])} />
        ) : needs.data?.required.includes(kind) ? (
          <Alert key={kind}>This project has no {KIND[kind] ?? kind} connection linked, and a routine here watches one. Link one to the project first, then add the recipe.</Alert>
        ) : (
          <Alert key={kind} tone="warn">
            This project has no {KIND[kind] ?? kind} connection linked. Link one first, or add the recipe without it and link one later.
          </Alert>
        ),
      )}
      {needs.error ? <Alert>{needs.error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!needs.data || needs.data.required.some((k) => !kinds[k]?.length)} onClick={add}>
          Add to this project
        </Button>
        <Button kind="ghost" onClick={onCancel}>
          Back
        </Button>
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
