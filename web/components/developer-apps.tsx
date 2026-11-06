"use client";

import { useState } from "react";

import { GoogleSettings } from "@/components/google";
import { Alert, Badge, Button, Card, ConfirmButton, Details, Field, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface AppInfo {
  id: string;
  name: string;
  client_id: string;
  options: Record<string, string>;
  redirect: string;
  accounts: number;
}

interface OptionInfo {
  key: string;
  label: string;
  hint: string;
  default: string;
  choices: [string, string][];
}

interface ProviderInfo {
  key: string;
  name: string;
  console: string;
  redirect: string;
  steps: string[];
  scopes: string[];
  options: OptionInfo[];
  note: string;
  listed: boolean;
  client_review: boolean;
  apps: AppInfo[];
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

function Check({ provider, app }: { provider: string; app: AppInfo }) {
  const [said, setSaid] = useState<{ ok: boolean; message: string } | null>(null);
  const [busy, setBusy] = useState(false);
  async function check() {
    setBusy(true);
    try {
      setSaid(await api<{ ok: boolean; message: string }>(`developer-apps/${provider}/apps/${app.id}/check`, { method: "POST" }));
    } catch (err) {
      setSaid({ ok: false, message: errorText(err) });
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Button kind="secondary" busy={busy} onClick={check}>
        Check it
      </Button>
      {said ? <Alert tone={said.ok ? "good" : undefined}>{said.message}</Alert> : null}
    </>
  );
}

function AddApp({ provider, onSaved }: { provider: ProviderInfo; onSaved: () => void }) {
  const [name, setName] = useState(provider.apps.length ? "" : "My app");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [options, setOptions] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`developer-apps/${provider.key}/apps`, { json: { name, client_id: clientId, client_secret: secret, options } });
      setClientId("");
      setSecret("");
      onSaved();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <Field label="Name" value={name} onChange={setName} hint="Say, whose it is." />
      <Field label="Client ID" value={clientId} onChange={setClientId} />
      <Field label="Client secret" type="password" value={secret} onChange={setSecret} hint="Kept in the vault. It never comes back to this page." />
      {provider.options.map((o) =>
        o.choices.length ? (
          <Select key={o.key} label={o.label} value={options[o.key] ?? o.default} onChange={(v) => setOptions((now) => ({ ...now, [o.key]: v }))} options={o.choices} hint={o.hint} />
        ) : (
          <Field key={o.key} label={o.label} value={options[o.key] ?? o.default} onChange={(v) => setOptions((now) => ({ ...now, [o.key]: v }))} hint={o.hint} />
        ),
      )}
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!name.trim() || !clientId.trim() || !secret.trim()} onClick={save}>
        Save this app
      </Button>
    </div>
  );
}

/** Whether the vendor has reviewed this app. Only that can let a client's account sign in. */
function Review({ provider, app, onChange }: { provider: ProviderInfo; app: AppInfo; onChange: () => void }) {
  const option = provider.options.find((o) => o.key === "client_accounts");
  const [error, setError] = useState<string | null>(null);
  if (!provider.client_review || !option) return null;
  async function choose(value: string) {
    setError(null);
    try {
      await api(`developer-apps/${provider.key}/apps/${app.id}/options`, { method: "PUT", json: { options: { ...app.options, client_accounts: value } } });
      onChange();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <>
      <Select label={option.label} value={app.options.client_accounts ?? option.default} onChange={choose} options={option.choices} hint={option.hint} />
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
}

function Provider({ provider, onChange }: { provider: ProviderInfo; onChange: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function remove(app: AppInfo) {
    setError(null);
    try {
      await api(`developer-apps/${provider.key}/apps/${app.id}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="font-semibold">{provider.name}</h3>
        {provider.apps.length ? <Badge tone="good">{provider.apps.length === 1 ? "1 app" : `${provider.apps.length} apps`}</Badge> : <Badge>Not set up</Badge>}
      </div>
      {provider.note ? <p className="mt-1 text-sm text-muted">{provider.note}</p> : null}
      {provider.apps.length ? (
        <ul className="mt-3 space-y-3">
          {provider.apps.map((app) => (
            <li key={app.id} className="space-y-2 rounded-xl border border-line p-3 text-sm">
              <p>
                <strong>{app.name}</strong> <span className="text-muted">· {app.accounts === 1 ? "1 account" : `${app.accounts} accounts`}</span>
              </p>
              <p className="break-all font-mono text-xs text-muted">{app.client_id}</p>
              <p className="text-xs text-muted">
                Comes back to <code className="break-all select-all">{app.redirect}</code>
              </p>
              <Review provider={provider} app={app} onChange={onChange} />
              <div className="flex flex-wrap items-center gap-2">
                <Check provider={provider.key} app={app} />
                <ConfirmButton question={app.accounts ? `Remove it? ${app.accounts} account(s) stop working at their next refresh.` : "Remove this app?"} confirm="Remove it" onConfirm={() => remove(app)}>
                  Remove
                </ConfirmButton>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="mt-3">
        <Details label={provider.apps.length ? "Add another app" : "Set it up"} defaultOpen={false}>
          <div className="space-y-3">
            <ol className="list-decimal space-y-2 pl-5 text-sm">
              {provider.steps.map((step) => (
                <li key={step} className="break-words">
                  {step}
                </li>
              ))}
            </ol>
            <p className="text-sm">
              <a href={provider.console} target="_blank" rel="noopener noreferrer" className="text-accent underline">
                Open {provider.name}&apos;s page
              </a>
            </p>
            <p className="text-sm">
              Address to paste: <code className="rounded bg-sunken px-1 py-0.5 text-xs break-all select-all">{provider.redirect}</code>
            </p>
            {provider.scopes.length ? (
              <div className="text-sm">
                <p>Scopes:</p>
                <p className="mt-1 rounded bg-sunken p-2 font-mono text-xs break-words select-all">{provider.scopes.join(" ")}</p>
              </div>
            ) : null}
            <AddApp provider={provider} onSaved={onChange} />
          </div>
        </Details>
      </div>
    </Card>
  );
}

/** Settings › Developer apps: your own registered apps, one set per vendor. */
export function DeveloperApps() {
  const all = useApi<{ desktop: boolean; providers: ProviderInfo[] }>("developer-apps");
  return (
    <div className="space-y-4">
      <Card>
        <h3 className="mb-2 font-semibold">Google</h3>
        <GoogleSettings />
      </Card>
      {(all.data?.providers ?? [])
        .filter((provider) => provider.listed)
        .map((provider) => (
          <Provider key={provider.key} provider={provider} onChange={all.reload} />
        ))}
      {all.error ? <Alert>{all.error}</Alert> : null}
    </div>
  );
}
