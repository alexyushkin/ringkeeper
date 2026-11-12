"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Card, Field, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

type Kind = "anthropic" | "bedrock" | "vertex" | "foundry";

interface PlaceValues {
  region?: string;
  profile?: string;
  project?: string;
  resource?: string;
  use_login?: boolean;
  models?: Record<string, string>;
}

interface Places {
  default: Kind;
  places: Partial<Record<Kind, PlaceValues>>;
  stored: Kind[];
}

const NAMES: [Kind, string][] = [
  ["anthropic", "Anthropic (API key or Claude subscription)"],
  ["bedrock", "Amazon Bedrock"],
  ["vertex", "Google Vertex AI"],
  ["foundry", "Microsoft Foundry"],
];
const HELP: Record<Exclude<Kind, "anthropic">, string> = {
  bedrock: "In the Amazon Bedrock console, submit the Anthropic use case form once. Then use a Bedrock API key, or an AWS profile from this computer: its short-lived credentials are read each time a session starts.",
  vertex: "Enable Vertex AI and the Claude models in Model Garden, then make a service account with the Vertex AI User role and download its key file.",
  foundry: "Create the resource in the Microsoft Foundry portal and a deployment per Claude model. Use its API key, or your Azure CLI login.",
};
const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Settings › Claude: where Claude runs, for workers, the architect and the router. */
export function ClaudePlace() {
  const saved = useApi<Places>("claude/place");
  const [kind, setKind] = useState<Kind | null>(null);
  const [values, setValues] = useState<PlaceValues>({});
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<{ ok: boolean; text: string } | null>(null);
  const chosen = kind ?? saved.data?.default ?? "anthropic";

  useEffect(() => {
    setValues(saved.data?.places[chosen] ?? {});
    setKey("");
  }, [chosen, saved.data]);

  const set = (name: keyof PlaceValues, value: string | boolean) => setValues((now) => ({ ...now, [name]: value }));
  const pin = (family: string, value: string) => setValues((now) => ({ ...now, models: { ...now.models, [family]: value.trim() } }));
  const body = () => ({ kind: chosen, ...values, models: values.models ?? {} });

  async function save() {
    setBusy(true);
    setSaid(null);
    try {
      await api("claude/place", { method: "PUT", json: { ...body(), key: key.trim() || null } });
      setKey("");
      saved.reload();
      setSaid({ ok: true, text: "Saved. New work runs here." });
    } catch (err) {
      setSaid({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  async function check() {
    setBusy(true);
    setSaid(null);
    try {
      const found = await api<{ ok: boolean; message: string }>("claude/place/check", { json: body() });
      setSaid({ ok: found.ok, text: found.message });
    } catch (err) {
      setSaid({ ok: false, text: errorText(err) });
    } finally {
      setBusy(false);
    }
  }

  const hasKey = saved.data?.stored.includes(chosen);
  return (
    <Card anchor="claude-place">
      <h3 className="mb-2 font-semibold">Where Claude runs</h3>
      <p className="mb-3 text-sm text-muted">Tasks, the architect and the router all run here. A project can use another on its own page.</p>
      <div className="space-y-3">
        <Select label="Claude runs on" value={chosen} onChange={(v) => { setKind(v as Kind); setSaid(null); }} options={NAMES} />
        {chosen === "anthropic" ? (
          saved.data && saved.data.default !== "anthropic" ? (
            <Button kind="primary" busy={busy} onClick={save}>
              Run on Anthropic
            </Button>
          ) : null
        ) : (
          <>
            <p className="text-sm text-muted">{HELP[chosen]}</p>
            {chosen === "bedrock" ? (
              <>
                <Field label="AWS region" value={values.region ?? ""} onChange={(v) => set("region", v)} placeholder="us-east-1" />
                <Field label="AWS profile (optional)" value={values.profile ?? ""} onChange={(v) => set("profile", v)} hint="On this computer. Leave it empty to use an API key." />
                {values.profile ? null : <Field label="Bedrock API key" type="password" value={key} onChange={setKey} hint={hasKey ? "One is saved. Paste a new one to replace it." : "Kept in the vault."} />}
              </>
            ) : null}
            {chosen === "vertex" ? (
              <>
                <Field label="Google Cloud project ID" value={values.project ?? ""} onChange={(v) => set("project", v)} />
                <Field label="Region" value={values.region ?? ""} onChange={(v) => set("region", v)} placeholder="global" />
                <label className="block space-y-1 text-sm font-medium">
                  <span>Service account key file</span>
                  <input
                    type="file"
                    accept=".json"
                    className="block w-full text-sm font-normal"
                    onChange={(e) => {
                      void e.target.files?.[0]?.text().then(setKey);
                    }}
                  />
                  <span className="block text-xs font-normal text-muted">{hasKey ? "One is saved. Choose a new one to replace it." : "Kept in the vault. A task's worker gets a copy in its own folder while it runs."}</span>
                </label>
              </>
            ) : null}
            {chosen === "foundry" ? (
              <>
                <Field label="Resource name" value={values.resource ?? ""} onChange={(v) => set("resource", v)} hint="The name alone, not its address." />
                <Toggle label="Use my Azure CLI login" checked={Boolean(values.use_login)} onChange={(on) => set("use_login", on)} hint="Read each time a session starts, instead of a key." />
                {values.use_login ? null : <Field label="Foundry API key" type="password" value={key} onChange={setKey} hint={hasKey ? "One is saved. Paste a new one to replace it." : "Kept in the vault."} />}
              </>
            ) : null}
            <fieldset className="space-y-2">
              <legend className="text-sm font-medium">Models on this platform</legend>
              <p className="text-xs text-muted">Optional: the IDs here, if they differ. {chosen === "foundry" ? "Foundry needs these: they're your deployment names." : "Empty: Claude Code's defaults for this platform."}</p>
              {["opus", "sonnet", "haiku"].map((family) => (
                <Field key={family} label={family[0]!.toUpperCase() + family.slice(1)} value={values.models?.[family] ?? ""} onChange={(v) => pin(family, v)} />
              ))}
            </fieldset>
            <div className="flex flex-wrap gap-2">
              <Button kind="primary" busy={busy} onClick={save}>
                Save and use it
              </Button>
              <Button kind="secondary" busy={busy} onClick={check}>
                Check it
              </Button>
            </div>
            <p className="text-xs text-muted">Check it sends one tiny request, billed by your cloud. Save first if you changed the key.</p>
          </>
        )}
        {said ? <Alert tone={said.ok ? "good" : undefined}>{said.text}</Alert> : null}
      </div>
    </Card>
  );
}

/** A project's own place, on its page. */
export function ProjectClaudePlace({ project }: { project: string }) {
  const places = useApi<Places>("claude/place");
  const mine = useApi<{ kind: Kind | null }>(`projects/${project}/claude-place`);
  const [error, setError] = useState<string | null>(null);
  const configured = NAMES.filter(([kind]) => kind === "anthropic" || places.data?.places[kind]);
  if (configured.length < 2) return null;

  async function change(kind: string) {
    setError(null);
    try {
      await api(`projects/${project}/claude-place`, { method: "PUT", json: { kind: kind || null } });
      mine.reload();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="space-y-2">
      <Select label="Where Claude runs for this project" value={mine.data?.kind ?? ""} onChange={(v) => void change(v)} options={[["", "The default, from Settings"], ...configured]} />
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
