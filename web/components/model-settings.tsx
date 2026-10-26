"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Field, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";

interface Offered {
  listed: boolean;
  error: string | null;
  models: { id: string; name: string; note: string; cost: string; recommended: boolean }[];
}

type Values = Record<string, unknown>;

/** Which model and effort each role uses. Effort is the most a role uses: a worker's
 * follows its task's size up to it. */
const ROLES: { model: string; effort?: string; label: string; hint?: string }[] = [
  { model: "model_router", effort: "effort_router", label: "Sorting requests" },
  { model: "model_architect", effort: "effort_architect", label: "Planning features", hint: "Big features plan on the Big tasks model." },
  { model: "model_low", label: "Small tasks" },
  { model: "model_medium", label: "Normal tasks" },
  { model: "model_high", label: "Big tasks" },
  { model: "model_worker", effort: "effort_worker", label: "Tasks without a size", hint: "Its effort is also the most any task's worker uses." },
];
const EFFORTS: [string, string][] = [
  ["low", "Low effort"],
  ["medium", "Medium effort"],
  ["high", "High effort"],
  ["xhigh", "Extra-high effort"],
  ["max", "Maximum effort"],
];
const CUSTOM = "__custom__";

export function ModelSettings({ values, onChange }: { values: Values; onChange: (changes: Values) => void }) {
  const offered = useApi<Offered>("models");
  const models = offered.data?.models ?? [];
  return (
    <div className="space-y-4">
      {offered.data?.error ? <Alert tone="warn">{offered.data.error} You can still pick a recommended model or type one.</Alert> : null}
      {ROLES.map((role) => (
        <RoleRow key={role.model} role={role} values={values} models={models} onChange={onChange} />
      ))}
    </div>
  );
}

function RoleRow({ role, values, models, onChange }: { role: (typeof ROLES)[number]; values: Values; models: Offered["models"]; onChange: (changes: Values) => void }) {
  const current = String(values[role.model] ?? "");
  const known = models.some((m) => m.id === current);
  const [custom, setCustom] = useState(!known && models.length > 0);
  const [typed, setTyped] = useState(current);
  useEffect(() => {
    setTyped(current);
    setCustom(models.length > 0 && !models.some((m) => m.id === current));
  }, [current, models]);

  const options: [string, string][] = [
    ...models.map((m): [string, string] => [m.id, m.recommended ? `${m.name} · ${m.cost} · ${m.note}` : m.id]),
    [CUSTOM, "Custom…"],
  ];
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <div className="grid gap-2 sm:grid-cols-[2fr_1fr]">
        <Select
          label={role.label}
          value={custom ? CUSTOM : current}
          onChange={(next) => (next === CUSTOM ? setCustom(true) : onChange({ [role.model]: next }))}
          options={options}
          hint={role.hint}
        />
        {role.effort ? <Select label="Effort" value={String(values[role.effort] ?? "high")} onChange={(next) => onChange({ [role.effort!]: next })} options={EFFORTS} /> : null}
      </div>
      {custom ? (
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Field label="Model id" value={typed} onChange={setTyped} placeholder="claude-…" />
          </div>
          <Button disabled={!typed.trim() || typed.trim() === current} onClick={() => onChange({ [role.model]: typed.trim() })}>
            Save
          </Button>
        </div>
      ) : null}
    </div>
  );
}

/** A dollar amount (the spending cap per task). */
export function MoneySetting({ label, value, onSave, hint }: { label: string; value: number; onSave: (n: number) => void; hint?: string }) {
  const [text, setText] = useState(value.toFixed(2));
  useEffect(() => setText(value.toFixed(2)), [value]);
  const amount = Number(text);
  const valid = /^\d+(\.\d{1,2})?$/.test(text) && amount > 0;
  return (
    <div className="flex items-end gap-2">
      <div className="flex-1">
        <Field label={label} value={text} onChange={setText} inputMode="decimal" hint={hint} />
      </div>
      <Button disabled={!valid || amount === value} onClick={() => onSave(amount)}>
        Save
      </Button>
    </div>
  );
}
