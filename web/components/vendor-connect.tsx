"use client";

import Link from "@/components/link";
import { useEffect, useState } from "react";

import { defaultTitle, EnvChoice, nameFor, useTakenNames } from "@/components/connection-form";
import { Alert, Button, Field, Select, Toggle } from "@/components/ui";
import { SHARED_OWNER } from "@/components/shared-tools";
import { api, useApi } from "@/lib/client/api";

export interface VendorSecret {
  key: string;
  label: string;
  hint: string;
  hidden: boolean;
  required: boolean;
}

export interface VendorInfo {
  key: string;
  name: string;
  category: string;
  sub: string;
  words: string[];
  steps: string;
  key_url: string;
  note: string;
  provider: string;
  knows_env: boolean;
  unverified: string;
  secrets: VendorSecret[];
}

interface ProviderApp {
  id: string;
  name: string;
  options: Record<string, string>;
}

interface ProviderInfo {
  key: string;
  name: string;
  access: { key: string; label: string }[];
  client_review: boolean;
  apps: ProviderApp[];
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

interface Target {
  vendor: VendorInfo;
  client?: string;
  project?: string;
  shared?: boolean;
  onAdded: (name: string) => void;
}

/** A business system: its key pasted, or an account signed in through your own app. */
export function VendorConnect(target: Target) {
  const { vendor } = target;
  return (
    <div className="space-y-3 rounded-xl border border-line p-3">
      {vendor.unverified ? <Alert tone="warn">Unverified: {vendor.unverified}</Alert> : null}
      {vendor.note ? <p className="text-sm text-muted">{vendor.note}</p> : null}
      {vendor.provider ? <SignIn {...target} /> : <PasteKey {...target} />}
    </div>
  );
}

function PasteKey({ vendor, client, project, shared = false, onAdded }: Target) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [typed, setTyped] = useState<string | null>(null);
  const [env, setEnv] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const taken = useTakenNames();
  const title = typed ?? defaultTitle(vendor.name, "", shared ? undefined : client);
  const name = nameFor(title, shared ? undefined : client, taken);
  const filled = vendor.secrets.every((s) => !s.required || (values[s.key] ?? "").trim());

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const secret = JSON.stringify({ vendor: vendor.key, ...Object.fromEntries(Object.entries(values).map(([k, v]) => [k, v.trim()])) });
      await api("connections", { json: { name, title: title.trim(), type: "vendor", env, owner: shared ? SHARED_OWNER : client, secret, shared } });
      setValues({});
      if (project) await api(`projects/${project}/connections/${name}`, { method: "PUT", json: { is_default: false } });
      onAdded(name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <p className="text-sm text-muted">
        {vendor.steps}{" "}
        {vendor.key_url ? (
          <a href={vendor.key_url} target="_blank" rel="noreferrer" className="text-accent underline">
            Open it
          </a>
        ) : null}
      </p>
      <EnvChoice value={env} onChange={setEnv} />
      {vendor.knows_env ? <p className="text-xs text-muted">The key says which it is, so this has to match it.</p> : null}
      <Field label="Name" value={title} onChange={setTyped} hint="As it shows in Ringkeeper. Change it if you like." />
      {vendor.secrets.map((s) => (
        <Field key={s.key} label={s.label} type={s.hidden ? "password" : "text"} value={values[s.key] ?? ""} onChange={(v) => setValues((now) => ({ ...now, [s.key]: v }))} hint={s.hint} />
      ))}
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!title.trim() || !env || !filled} onClick={save}>
        Connect
      </Button>
    </>
  );
}

function SignIn({ vendor, client, project, shared = false, onAdded }: Target) {
  const all = useApi<{ desktop: boolean; providers: ProviderInfo[] }>("developer-apps");
  const provider = all.data?.providers.find((p) => p.key === vendor.provider);
  const [appId, setAppId] = useState("");
  const [access, setAccess] = useState<string[]>(["mail_read", "calendar_read", "files_read"]);
  const [env, setEnv] = useState("");
  const [everywhere, setEverywhere] = useState(false);
  const [state, setState] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!state) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<{ state: string; name: string | null; error: string | null }>(`developer-apps/signin/${state}`)
        .then((found) => {
          if (found.state === "done" && found.name) {
            setState(null);
            onAdded(found.name);
          } else if (found.state === "failed") {
            setState(null);
            setError(found.error ?? "Not connected.");
          } else if (Date.now() > deadline) setState(null);
        })
        .catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [state, onAdded]);

  if (!provider) return null;
  if (!provider.apps.length) {
    return (
      <p className="text-sm text-muted">
        {vendor.name} signs in through an app of your own. First add it:{" "}
        <Link href="/settings#developer-apps" className="text-accent underline">
          Settings › Developer apps
        </Link>
        .
      </p>
    );
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const body = { vendor: vendor.key, access, owner: shared ? undefined : client, shared, all_projects: shared && everywhere, env, project, app: appId || undefined };
      const started = await api<{ url: string; state: string }>("developer-apps/signin", { json: body });
      window.open(started.url, "_blank", "noopener");
      setState(started.state);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const toggle = (key: string, on: boolean) => setAccess((now) => (on ? [...now, key] : now.filter((k) => k !== key)));
  const chosen = provider.apps.find((a) => a.id === appId) ?? provider.apps[0];
  const needsReview = provider.client_review && !shared && chosen?.options.client_accounts !== "approved";
  if (needsReview) {
    return (
      <Alert tone="warn">
        Needs Meta review: a client&apos;s account can&apos;t sign in until Meta has reviewed your app. Add {vendor.name} as a shared tool for your own accounts. Once Meta approves the app, say so in{" "}
        <Link href="/settings#developer-apps" className="underline">
          Settings › Developer apps
        </Link>
        .
      </Alert>
    );
  }
  return (
    <>
      {provider.apps.length > 1 ? <Select label="Sign in through" value={appId || (provider.apps[0]?.id ?? "")} onChange={setAppId} options={provider.apps.map((a): [string, string] => [a.id, a.name])} hint={`Which of your ${provider.name} apps.`} /> : null}
      {provider.access.length && vendor.key === provider.key ? (
        <fieldset className="space-y-1">
          <legend className="text-sm font-medium">What may it be used for?</legend>
          {provider.access.map((a) => (
            <label key={a.key} className="flex items-start gap-2 text-sm">
              <input type="checkbox" checked={access.includes(a.key)} onChange={(e) => toggle(a.key, e.target.checked)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
              <span>{a.label}</span>
            </label>
          ))}
        </fieldset>
      ) : null}
      <EnvChoice value={env} onChange={setEnv} />
      {shared ? <Toggle label="Use in all projects" checked={everywhere} onChange={setEverywhere} hint="Every client's projects, new ones too." /> : null}
      {state ? (
        <Alert tone="good">Finish signing in on {vendor.name}&apos;s page that opened in your browser. This updates when it&apos;s done.</Alert>
      ) : (
        <Button kind="primary" busy={busy} disabled={!env || (vendor.key === provider.key && provider.access.length > 0 && !access.length)} onClick={start}>
          Sign in with {vendor.name}
        </Button>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </>
  );
}
