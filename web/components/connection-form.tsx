"use client";

import { useId, useState } from "react";

import type { ConnectionData } from "@/components/forms";
import { SHARED_OWNER } from "@/components/shared-tools";
import { Alert, Button, Field, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientName } from "@/lib/client/scope";
import { slugify } from "@/lib/client/words";

/** A connection as people read it: its title, else its name. */
export function connectionTitle(connection: { name: string; title?: string | null }): string {
  return connection.title?.trim() || connection.name;
}

/** Each connection's title by its name, for showing a name the server sent. */
export function useConnectionTitles(): (name: string) => string {
  const list = useApi<{ name: string; title?: string | null }[]>("connections").data ?? [];
  const titles = new Map(list.map((c) => [c.name, connectionTitle(c)]));
  return (name: string) => titles.get(name) ?? name;
}

/** A tool as people read it: "mcp__ringkeeper-database__execute" -> "Database: execute". */
export function toolLabel(name: string): string {
  if (name === "ToolSearch") return "Looked up a tool";
  const mcp = /^mcp__(?:ringkeeper-)?(.+?)__(.+)$/.exec(name);
  if (!mcp) return `Used ${name}`;
  const server = (mcp[1] ?? "").replace(/[-_]+/g, " ");
  return `${server.charAt(0).toUpperCase()}${server.slice(1)}: ${(mcp[2] ?? "").replace(/_/g, " ")}`;
}

/** "n8n · n8n.geninfos.com": the service, then the account or host (else the client's name). */
export function defaultTitle(service: string, detail: string, owner?: string): string {
  const what = detail.trim() || (owner ? clientName(owner) : "");
  return what ? `${service} · ${what}` : service;
}

/** The host in an address or a connection string (never its password). */
export function hostOf(address: string): string {
  const text = address.trim();
  if (!text) return "";
  try {
    return new URL(text.includes("://") ? text : `https://${text}`).hostname;
  } catch {
    const at = text.lastIndexOf("@");
    return (at >= 0 ? text.slice(at + 1) : text).split(/[:/?]/)[0] ?? "";
  }
}

/** The short name the AI uses for it, from its title, not taken by another connection. */
export function nameFor(title: string, owner: string | undefined, taken: string[]): string {
  const who = owner ? slugify(clientName(owner)) : "";
  const what = slugify(title);
  const named = !who || what.startsWith(who) ? what : `${who}-${what}`; // never "acme-acme-database"
  const base = named.slice(0, 56).replace(/-+$/, "") || "connection";
  let name = base;
  for (let n = 2; taken.includes(name); n += 1) name = `${base}-${n}`;
  return name;
}

export function useTakenNames(): string[] {
  return (useApi<{ name: string }[]>("connections").data ?? []).map((c) => c.name);
}

// What each answer means. A built-in service's changes run on test data; an add-on
// tool (MCP) waits for approval on anything not marked read-only either way.
const ENV_CHOICES: [string, string, string, string][] = [
  ["prod", "Yes, live client data", "Production: every change waits for your approval.", "Production: anything not marked read-only waits for your approval."],
  ["staging", "A copy of it", "Staging: changes run without asking.", "Staging: anything not marked read-only waits for your approval."],
  ["dev", "No, test data", "Test: changes run without asking (texts and emails still wait).", "Test: anything not marked read-only waits for your approval."],
];

/** "Is this live client data?" asked every time, with nothing chosen until it's answered. */
export function EnvChoice({ value, onChange, tool = false }: { value: string; onChange: (env: string) => void; tool?: boolean }) {
  const id = useId();
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Is this live client data?</legend>
      <div className="grid gap-2 sm:grid-cols-3">
        {ENV_CHOICES.map(([key, label, builtIn, addOn]) => (
          <label key={key} className={`flex cursor-pointer gap-2 rounded-xl border p-3 text-sm ${value === key ? "border-accent bg-accent-soft" : "border-line bg-surface hover:border-accent"}`}>
            <input type="radio" name={id} value={key} checked={value === key} onChange={() => onChange(key)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span>
              <span className="block font-medium">{label}</span>
              <span className="block text-xs text-muted">{tool ? addOn : builtIn}</span>
            </span>
          </label>
        ))}
      </div>
      {value ? null : <p className="text-xs text-muted">Choose one: it decides which changes wait for you.</p>}
    </fieldset>
  );
}

interface ServiceInfo {
  short: string; // in a connection's title: "n8n · n8n.geninfos.com"
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
    short: "Database",
    label: "Database (Supabase, Neon or any Postgres)",
    example: "General Informatics database",
    get: [
      ["Supabase", "https://supabase.com/dashboard/projects"],
      ["Neon", "https://console.neon.tech/app/projects"],
    ],
    steps: "Open the project and click Connect. In Supabase, use the Session pooler string; in Neon, the connection string. It starts with postgresql://",
    secret: [{ key: "", label: "Connection string", placeholder: "postgresql://…", hidden: true }],
  },
  n8n: {
    short: "n8n",
    label: "n8n (automations)",
    example: "General Informatics automations",
    get: [["n8n's API settings", "https://docs.n8n.io/api/authentication/"]],
    steps: "In n8n, open Settings › n8n API and create an API key. (n8n Cloud's free trial has no API.)",
    address: { label: "n8n address", placeholder: "https://n8n.geninfos.com" },
    secret: [{ key: "", label: "API key", hidden: true }],
  },
  airtable: {
    short: "Airtable",
    label: "Airtable",
    example: "General Informatics CRM",
    get: [["Airtable tokens", "https://airtable.com/create/tokens"]],
    steps: "Create a personal access token with the data.records and schema.bases scopes you want (read only, or read and write), and add only this client's bases. It starts with pat.",
    secret: [{ key: "", label: "Personal access token", placeholder: "pat…", hidden: true }],
  },
  vercel: {
    short: "Vercel",
    label: "Vercel (deployments)",
    example: "geninfos.com hosting",
    get: [["Vercel tokens", "https://vercel.com/account/tokens"]],
    steps: "Create a token scoped to this client's team (or one project), with an expiry date.",
    secret: [{ key: "", label: "Access token", hidden: true }],
    note: "Ringkeeper can read deployments and build logs, and redeploy or roll back. It never sees your project's secret settings.",
  },
  twilio: {
    short: "Twilio",
    label: "Twilio (texts and calls)",
    example: "General Informatics texting",
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
    short: "GitHub",
    label: "GitHub",
    example: "General Informatics code",
    get: [
      ["a fine-grained token", "https://github.com/settings/personal-access-tokens/new"],
      ["a classic token", "https://github.com/settings/tokens/new"],
    ],
    steps:
      "Fine-grained (best): Resource owner = the client's organization (or you), Repository access = Only select repositories (this client's), then Repository permissions: Contents, Issues, Pull requests and Metadata (read and write to let Ringkeeper push and open issues and PRs; read-only to only read). Leave everything else, settings above all, at No access. Set an expiry. A classic token (scope: repo) only if the organization doesn't allow fine-grained ones.",
    address: { label: "GitHub Enterprise Server address", placeholder: "empty for github.com, or https://github.geninfos.com", optional: true },
    secret: [{ key: "", label: "Token", placeholder: "github_pat_… or ghp_…", hidden: true }],
    note: "Reading runs. Issues, comments, pull requests, merges and pushes wait for your approval; a merge needs the name typed. Pin a repository per project to keep each project on its own repo.",
  },
  gmail: {
    short: "Gmail",
    label: "Gmail",
    example: "General Informatics inbox",
    get: [["Google app passwords", "https://myaccount.google.com/apppasswords"]],
    steps: "Create an app password (Google needs 2-Step Verification on). A work admin may have turned them off, and accounts with Advanced Protection or security-key-only sign-in can't use them.",
    secret: [
      { key: "address", label: "Gmail address", placeholder: "name@gmail.com" },
      { key: "app_password", label: "App password", placeholder: "abcd efgh ijkl mnop", hidden: true },
    ],
    note: "Every email waits for your approval before it's sent. Saving a draft doesn't.",
  },
};

/** The account or host a connection's title names, from what was typed so far. */
function detailFor(type: string, address: string, parts: Record<string, string>): string {
  if (type === "n8n" || type === "github") return hostOf(address);
  if (type === "supabase") return hostOf(parts[""] ?? "");
  if (type === "gmail") return (parts.address ?? "").trim();
  return "";
}

/**
 * Add a connection to a built-in service (`type`; without it, a Service choice);
 * with `project`, link it to that project too (as its default for that service when
 * it isn't production and there's none yet). The secret goes in once and is never
 * shown again. Its name is readable ("n8n · n8n.geninfos.com") and can be changed.
 */
export function ConnectionForm({
  client,
  project,
  shared = false,
  type: fixed,
  onSaved,
}: {
  client?: string;
  project?: string;
  shared?: boolean; // yours: any client's projects (or all of them), not one client's
  type?: string; // the service, already chosen
  onSaved: (connection: ConnectionData) => void;
}) {
  const [type, setType] = useState(fixed ?? "supabase");
  const [all, setAll] = useState(false);
  const [typed, setTyped] = useState<string | null>(null); // null: the suggested title
  const [owner, setOwner] = useState(client ?? "");
  const [env, setEnv] = useState("");
  const [address, setAddress] = useState("");
  const [parts, setParts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const taken = useTakenNames();
  const service = SERVICES[type] ?? SERVICES.supabase!;
  const title = typed ?? defaultTitle(service.short, detailFor(type, address, parts), shared ? undefined : owner);
  const name = nameFor(title, shared ? undefined : owner, taken);
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
          name, title: title.trim(), type, env, owner: shared ? SHARED_OWNER : owner.trim(),
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
      {fixed ? null : (
        <Select
          label="Service"
          value={type}
          onChange={(next) => {
            setType(next);
            setParts({});
          }}
          options={Object.entries(SERVICES).map(([key, info]): [string, string] => [key, info.label])}
        />
      )}
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
      {client || shared ? null : <Field label="Client" value={owner} onChange={setOwner} placeholder="General Informatics LLC" required />}
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
      <EnvChoice value={env} onChange={setEnv} />
      <Field label="Name" value={title} onChange={setTyped} placeholder={service.example} hint="As it shows in Ringkeeper. Change it if you like." required />
      {shared ? <Toggle label="Use in all projects" checked={all} onChange={setAll} hint="Every client's projects, new ones too." /> : null}
      {service.note ? <p className="text-sm text-muted">{service.note}</p> : null}
      {error ? <Alert>{error}</Alert> : null}
      <Button type="submit" kind="primary" busy={busy} disabled={!title.trim() || !env || !filled || (!shared && !owner.trim()) || (!!service.address && !service.address.optional && !address.trim())}>
        {project ? `Add and link to ${project}` : "Add connection"}
      </Button>
    </form>
  );
}
