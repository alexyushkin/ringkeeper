"use client";

import { useState } from "react";

import type { ConnectionData } from "@/components/forms";
import { SHARED_OWNER } from "@/components/shared-tools";
import { Alert, Button, Field, Select, Toggle } from "@/components/ui";
import { api } from "@/lib/client/api";
import { ENVS, slugify } from "@/lib/client/words";

interface ServiceInfo {
  label: string;
  example: string; // a name, for the Name field's placeholder
  get: [string, string][]; // where to get the credential: [link text, URL]
  steps: string; // what to do there
  address?: { label: string; placeholder: string; optional?: boolean };
  // One secret field, or several sent as JSON (the adapter reads them back).
  secret: { key: string; label: string; placeholder?: string; hidden?: boolean; optional?: boolean }[];
  note?: string;
}

/** The services a connection can be, and how to connect each (v1: API tokens only). */
export const SERVICES: Record<string, ServiceInfo> = {
  supabase: {
    label: "Database (Supabase, Neon or any Postgres)",
    example: "Acme database",
    get: [
      ["Supabase", "https://supabase.com/dashboard/projects"],
      ["Neon", "https://console.neon.tech/app/projects"],
    ],
    steps: "Open the project and click Connect. In Supabase, use the Session pooler string; in Neon, the connection string. It starts with postgresql://",
    secret: [{ key: "", label: "Connection string", placeholder: "postgresql://…", hidden: true }],
  },
  n8n: {
    label: "n8n (automations)",
    example: "Acme automations",
    get: [["n8n's API settings", "https://docs.n8n.io/api/authentication/"]],
    steps: "In n8n, open Settings › n8n API and create an API key. (n8n Cloud's free trial has no API.)",
    address: { label: "n8n address", placeholder: "https://n8n.acme.example" },
    secret: [{ key: "", label: "API key", hidden: true }],
  },
  airtable: {
    label: "Airtable",
    example: "Acme CRM",
    get: [["Airtable tokens", "https://airtable.com/create/tokens"]],
    steps: "Create a personal access token with the data.records and schema.bases scopes you want (read only, or read and write), and add only this client's bases. It starts with pat.",
    secret: [{ key: "", label: "Personal access token", placeholder: "pat…", hidden: true }],
  },
  vercel: {
    label: "Vercel (deployments)",
    example: "Acme site hosting",
    get: [["Vercel tokens", "https://vercel.com/account/tokens"]],
    steps: "Create a token scoped to this client's team (or one project), with an expiry date.",
    secret: [{ key: "", label: "Access token", hidden: true }],
    note: "Ringkeeper can read deployments and build logs, and redeploy or roll back. It never sees env variable values.",
  },
  twilio: {
    label: "Twilio (texts and calls)",
    example: "Acme texting",
    get: [["Twilio console", "https://console.twilio.com/"]],
    steps: "Copy the Account SID from the console's home page. Then Account › API keys & tokens › Create API key (Standard), and copy its SID and secret.",
    secret: [
      { key: "account_sid", label: "Account SID", placeholder: "AC…" },
      { key: "key_sid", label: "API key SID", placeholder: "SK…" },
      { key: "key_secret", label: "API key secret", hidden: true },
    ],
    note: "Every text waits for your approval before it's sent.",
  },
  github: {
    label: "GitHub",
    example: "Acme code",
    get: [
      ["a fine-grained token", "https://github.com/settings/personal-access-tokens/new"],
      ["a classic token", "https://github.com/settings/tokens/new"],
    ],
    steps:
      "Fine-grained (best): Resource owner = the client's organization (or you), Repository access = Only select repositories (this client's), then Repository permissions: Contents, Issues, Pull requests and Metadata (read and write to let Ringkeeper push and open issues and PRs; read-only to only read). Leave everything else, settings above all, at No access. Set an expiry. A classic token (scope: repo) only if the organization doesn't allow fine-grained ones.",
    address: { label: "GitHub Enterprise Server address", placeholder: "empty for github.com, or https://github.acme.example", optional: true },
    secret: [{ key: "", label: "Token", placeholder: "github_pat_… or ghp_…", hidden: true }],
    note: "Reading runs. Issues, comments, pull requests, merges and pushes wait for your approval; a merge needs the name typed. Pin a repository per project to keep each project on its own repo.",
  },
  gmail: {
    label: "Gmail",
    example: "Acme inbox",
    get: [["Google app passwords", "https://myaccount.google.com/apppasswords"]],
    steps: "Create an app password (Google needs 2-Step Verification on). A work admin may have turned them off, and accounts with Advanced Protection or security-key-only sign-in can't use them.",
    secret: [
      { key: "address", label: "Gmail address", placeholder: "name@gmail.com" },
      { key: "app_password", label: "App password", placeholder: "abcd efgh ijkl mnop", hidden: true },
    ],
    note: "Every email waits for your approval before it's sent. Saving a draft doesn't.",
  },
};

/**
 * Add a connection; with `project`, link it to that project too (as its default for
 * that service when it isn't production and there's none yet). The secret goes in
 * once and is never shown again.
 */
export function ConnectionForm({
  client,
  project,
  shared = false,
  onSaved,
}: {
  client?: string;
  project?: string;
  shared?: boolean; // yours: any client's projects (or all of them), not one client's
  onSaved: (connection: ConnectionData) => void;
}) {
  const [type, setType] = useState("supabase");
  const [all, setAll] = useState(false);
  const [title, setTitle] = useState("");
  const [owner, setOwner] = useState(client ?? "");
  const [env, setEnv] = useState("dev");
  const [address, setAddress] = useState("");
  const [parts, setParts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const name = slugify(title);
  const service = SERVICES[type] ?? SERVICES.supabase!;
  const filled = service.secret.every((f) => f.optional || (parts[f.key] ?? "").trim());

  function secretValue(): string {
    if (service.secret.length === 1) return parts[""] ?? "";
    return JSON.stringify(Object.fromEntries(service.secret.map((f) => [f.key, (parts[f.key] ?? "").trim()])));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await api<ConnectionData>("connections", {
        json: {
          name, type, env, owner: shared ? SHARED_OWNER : slugify(owner) || owner,
          base_url: service.address ? address.trim() || null : null, secret: secretValue(),
          shared, all_projects: shared && all,
        },
      });
      setParts({});
      if (project) {
        const link = (is_default: boolean) => api(`projects/${project}/connections/${saved.name}`, { method: "PUT", json: { is_default } });
        await (env === "prod" ? link(false) : link(true).catch(() => link(false)));
      }
      onSaved(saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={save} className="space-y-4" autoComplete="off">
      <Select
        label="Service"
        value={type}
        onChange={(next) => {
          setType(next);
          setParts({});
        }}
        options={Object.entries(SERVICES).map(([key, info]): [string, string] => [key, info.label])}
      />
      <div className="space-y-1 rounded-xl bg-sunken p-3 text-sm">
        <p>
          Where to get it:{" "}
          {service.get.map(([text, href], index) => (
            <span key={href}>
              {index ? " or " : ""}
              <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
                {text}
              </a>
            </span>
          ))}
        </p>
        <p className="text-muted">{service.steps}</p>
      </div>
      <Field label="Name" value={title} onChange={setTitle} placeholder={service.example} hint={name ? `Short name: ${name}` : undefined} required />
      {client || shared ? null : <Field label="Client" value={owner} onChange={setOwner} placeholder="Acme Inc." required />}
      {shared ? <Toggle label="Use in all projects" checked={all} onChange={setAll} hint="Every client's projects, new ones too." /> : null}
      <Select
        label="Kind of data"
        value={env}
        onChange={setEnv}
        options={ENVS}
        hint={env === "prod" ? "Changes to production are always held for your approval." : "Test data: changes can run without asking."}
      />
      {service.address ? (
        <Field
          label={service.address.optional ? `${service.address.label} (optional)` : service.address.label}
          value={address}
          onChange={setAddress}
          placeholder={service.address.placeholder}
          inputMode="url"
          required={!service.address.optional}
        />
      ) : null}
      {service.secret.map((f) => (
        <Field
          key={`${type}-${f.key}`}
          label={f.optional ? `${f.label} (optional)` : f.label}
          type={f.hidden ? "password" : "text"}
          value={parts[f.key] ?? ""}
          onChange={(v) => setParts((all) => ({ ...all, [f.key]: v }))}
          placeholder={f.placeholder}
          required={!f.optional}
        />
      ))}
      {service.note ? <p className="text-sm text-muted">{service.note}</p> : null}
      {error ? <Alert>{error}</Alert> : null}
      <Button type="submit" kind="primary" busy={busy} disabled={!name || !filled || (!shared && !owner.trim()) || (!!service.address && !service.address.optional && !address.trim())}>
        {project ? `Add and link to ${project}` : "Add connection"}
      </Button>
    </form>
  );
}
