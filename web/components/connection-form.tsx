"use client";

import { useId, useState } from "react";

import type { ConnectionData } from "@/components/forms";
import { SHARED_OWNER } from "@/components/shared-tools";
import { Alert, Button, Field, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientName, projectName } from "@/lib/client/scope";
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
  address?: { label: string; placeholder: string; optional?: boolean; value?: string };
  // One secret field, or several sent as JSON (the adapter reads them back).
  secret: SecretField[];
  note?: string;
  noteFor?: (parts: Record<string, string>) => string | undefined; // what depends on a choice
  type?: string; // a preset of another type ("http"); otherwise its own key
  defaults?: Record<string, string>; // what's chosen until it's changed, sent even if not shown
}

interface SecretField {
  key: string;
  label: string;
  placeholder?: string;
  hidden?: boolean;
  optional?: boolean;
  options?: [string, string][]; // a choice, not a text box
  when?: (parts: Record<string, string>) => boolean; // shown only then
  hint?: string;
}

const TEST_ADDRESS: SecretField = { key: "test_address", label: "Your own address for test sends", placeholder: "you@geninfos.com", optional: true, hint: "A test to this address runs without asking. Nothing else does." };
const EMAIL_NOTE = "Every email waits for your approval. To more than 10 people at once, you also type the connection's name, in the app. A test to your own address runs by itself.";
const AUTHS: [string, string][] = [
  ["bearer", "Bearer token"],
  ["header", "A key in a header"],
  ["basic", "Username and password"],
  ["query", "A key in the address"],
  ["none", "No sign-in"],
];
const usesKey = (parts: Record<string, string>) => ["bearer", "header", "query"].includes(parts.auth ?? "");
const API_FIELDS: SecretField[] = [
  { key: "auth", label: "How it signs in", options: AUTHS },
  { key: "token", label: "Key or token", hidden: true, when: usesKey },
  { key: "header_name", label: "Header name", placeholder: "X-Api-Key", when: (p) => p.auth === "header" },
  { key: "query_name", label: "Parameter name", placeholder: "api_key", when: (p) => p.auth === "query" },
  { key: "username", label: "Username", when: (p) => p.auth === "basic" },
  { key: "password", label: "Password", hidden: true, when: (p) => p.auth === "basic" },
  { key: "spec_url", label: "OpenAPI address", placeholder: "https://api.example.com/openapi.json", optional: true, hint: "If the API publishes one, the AI can see what it offers." },
];
const API_NOTE = "GET runs without asking. Anything else waits for your approval, even on test data. DELETE needs the name typed. Ringkeeper can't undo what an API call does.";
const MAILBOX_NOTES: Record<string, string> = {
  gmail: "Gmail wants an app password (it needs 2-Step Verification): myaccount.google.com/apppasswords.",
  outlook: "For Microsoft 365, use the Microsoft account tile instead: Microsoft has turned password sign-in off for IMAP there, and is doing the same for SMTP.",
  zoho: "Zoho wants an app password if two-factor sign-in is on, and IMAP turned on in Mail settings.",
  yahoo: "Yahoo wants an app password: Account security › Generate app password.",
};
const isCustom = (parts: Record<string, string>) => parts.preset === "custom";
const notBlob = (parts: Record<string, string>) => parts.provider !== "vercel_blob";

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
  mailbox: {
    short: "Mailbox",
    label: "Mailbox",
    example: "General Informatics inbox",
    get: [],
    steps: "Choose your provider. Most want an app password here, not your normal password.",
    defaults: { preset: "gmail", smtp_security: "ssl" },
    secret: [
      { key: "preset", label: "Provider", options: [["gmail", "Gmail"], ["outlook", "Outlook or Microsoft 365"], ["zoho", "Zoho Mail"], ["yahoo", "Yahoo Mail"], ["custom", "Another provider"]] },
      { key: "address", label: "Email address", placeholder: "you@geninfos.com" },
      { key: "password", label: "Password or app password", hidden: true },
      { key: "imap_host", label: "IMAP server", placeholder: "imap.example.com", when: isCustom },
      { key: "smtp_host", label: "SMTP server", placeholder: "smtp.example.com", when: isCustom },
      { key: "smtp_port", label: "SMTP port", placeholder: "465", optional: true, when: isCustom },
      { key: "smtp_security", label: "SMTP encryption", options: [["ssl", "SSL (usually port 465)"], ["starttls", "STARTTLS (usually port 587)"]], when: isCustom },
      TEST_ADDRESS,
    ],
    note: "Reading runs and never marks mail as read. Drafts, moving and sending wait for your approval; deleting, and sending to more than 10 people at once, need the name typed.",
    noteFor: (parts) => MAILBOX_NOTES[parts.preset ?? ""],
  },
  smtp: {
    short: "Email",
    label: "Email sending (SMTP)",
    example: "General Informatics email",
    get: [],
    steps: "From your email provider: the SMTP server, and a username and password or SMTP key. The connection is always encrypted.",
    defaults: { security: "starttls" },
    secret: [
      { key: "host", label: "SMTP server", placeholder: "smtp.example.com" },
      { key: "security", label: "Encryption", options: [["starttls", "STARTTLS (usually port 587)"], ["ssl", "SSL (usually port 465)"]] },
      { key: "port", label: "Port", placeholder: "587", optional: true },
      { key: "from_address", label: "From address", placeholder: "hello@geninfos.com" },
      { key: "username", label: "Username", placeholder: "Empty: the From address", optional: true },
      { key: "password", label: "Password or SMTP key", hidden: true },
      TEST_ADDRESS,
    ],
    note: EMAIL_NOTE,
  },
  resend: {
    short: "Resend",
    label: "Resend (email sending)",
    example: "General Informatics email",
    get: [["Resend API keys", "https://resend.com/api-keys"]],
    steps: "Create an API key. Sending access is enough to send; full access also lets the AI read domains and delivery. The From address must be on a domain you verified in Resend.",
    secret: [
      { key: "api_key", label: "API key", placeholder: "re_…", hidden: true },
      { key: "from_address", label: "From address", placeholder: "hello@geninfos.com" },
      TEST_ADDRESS,
    ],
    note: EMAIL_NOTE,
  },
  http: {
    short: "API",
    label: "Any API",
    example: "General Informatics API",
    get: [],
    steps: "Its address, and how it signs in. Its own documentation says both.",
    address: { label: "API address", placeholder: "https://api.example.com/v1" },
    defaults: { auth: "bearer" },
    secret: API_FIELDS,
    note: API_NOTE,
  },
  dataforseo: {
    type: "http",
    short: "DataForSEO",
    label: "DataForSEO",
    example: "General Informatics SEO data",
    get: [["DataForSEO API access", "https://app.dataforseo.com/api-access"]],
    steps: "Copy the API login and password from API Access. They aren't the ones you sign in with.",
    address: { label: "API address", placeholder: "https://api.dataforseo.com/v3", value: "https://api.dataforseo.com/v3" },
    defaults: { auth: "basic" },
    secret: [
      { key: "username", label: "API login" },
      { key: "password", label: "API password", hidden: true },
    ],
    note: "DataForSEO's calls are POSTs and most cost credits, so each one waits for your approval. Reading finished results with GET runs.",
  },
  formsonfire: {
    type: "http",
    short: "Forms On Fire",
    label: "Forms On Fire",
    example: "General Informatics forms",
    get: [],
    steps: "Unverified: this address and sign-in haven't been confirmed against Forms On Fire's own documentation. Check both in your Forms On Fire account: other regions and Appenate use their own address.",
    address: { label: "API address", placeholder: "https://secure.formsonfire.com/api/v2", value: "https://secure.formsonfire.com/api/v2" },
    defaults: { auth: "header" },
    secret: API_FIELDS,
    note: API_NOTE,
  },
  s3: {
    short: "Files",
    label: "File storage",
    example: "General Informatics files",
    get: [
      ["AWS access keys", "https://console.aws.amazon.com/iam/home#/security_credentials"],
      ["Cloudflare R2 tokens", "https://dash.cloudflare.com/?to=/:account/r2/api-tokens"],
      ["Vercel Blob", "https://vercel.com/dashboard/stores"],
    ],
    steps: "Make a key limited to this client's bucket. On R2, the Account ID is on the R2 overview page. On Vercel, open the Blob store and copy its read-write token.",
    defaults: { provider: "aws" },
    secret: [
      { key: "provider", label: "Service", options: [["aws", "AWS S3"], ["r2", "Cloudflare R2"], ["vercel_blob", "Vercel Blob"], ["custom", "Another S3-compatible service"]] },
      { key: "account_id", label: "Cloudflare account ID", when: (p) => p.provider === "r2" },
      { key: "endpoint", label: "Address", placeholder: "https://s3.example.com", when: (p) => p.provider === "custom" },
      { key: "region", label: "Region", placeholder: "us-east-1", optional: true, when: (p) => p.provider === "aws" || p.provider === "custom" },
      { key: "access_key", label: "Access key ID", when: notBlob },
      { key: "secret_key", label: "Secret access key", hidden: true, when: notBlob },
      { key: "token", label: "Read-write token", placeholder: "vercel_blob_rw_…", hidden: true, when: (p) => p.provider === "vercel_blob" },
      { key: "bucket", label: "Bucket", optional: true, when: notBlob },
    ],
    note: "Listing and reading run. An upload waits for your approval; deleting needs the name typed. Neither can be undone here. Pin a bucket and folder per project to keep each project on its own files.",
  },
};

/** The account or host a connection's title names, from what was typed so far. */
function detailFor(type: string, address: string, parts: Record<string, string>): string {
  if (type === "n8n" || type === "github" || type === "http") return hostOf(address);
  if (type === "smtp" || type === "resend") return (parts.from_address ?? "").trim();
  if (type === "mailbox") return (parts.address ?? "").trim();
  if (type === "s3") return (parts.bucket ?? "").trim();
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
  const [typedAddress, setAddress] = useState<string | null>(null); // null: the service's own
  const [typedParts, setParts] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const taken = useTakenNames();
  const service = SERVICES[type] ?? SERVICES.supabase!;
  const kind = service.type ?? type;
  const address = typedAddress ?? service.address?.value ?? "";
  const parts = { ...service.defaults, ...typedParts };
  const shownFields = service.secret.filter((f) => !f.when || f.when(parts));
  const title = typed ?? defaultTitle(service.short, detailFor(kind, address, parts), shared ? undefined : owner);
  const name = nameFor(title, shared ? undefined : owner, taken);
  const filled = shownFields.every((f) => f.optional || (parts[f.key] ?? "").trim());
  const choiceNote = service.noteFor?.(parts);

  function secretValue(): string {
    if (service.secret.length === 1 && !service.secret[0]?.key) return parts[""] ?? "";
    const sent = [...Object.keys(service.defaults ?? {}), ...shownFields.map((f) => f.key)];
    return JSON.stringify(Object.fromEntries(sent.map((key) => [key, (parts[key] ?? "").trim()])));
  }

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved = await api<ConnectionData>("connections", {
        json: {
          name, title: title.trim(), type: kind, env, owner: shared ? SHARED_OWNER : owner.trim(),
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
        {service.get.length ? (
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
        ) : null}
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
      {shownFields.map((f) =>
        f.options ? (
          <Select key={`${type}-${f.key}`} label={f.label} value={parts[f.key] ?? ""} onChange={(v) => setParts((all) => ({ ...all, [f.key]: v }))} options={f.options} />
        ) : (
          <Field
            key={`${type}-${f.key}`}
            label={f.optional ? `${f.label} (optional)` : f.label}
            type={f.hidden ? "password" : "text"}
            value={parts[f.key] ?? ""}
            onChange={(v) => setParts((all) => ({ ...all, [f.key]: v }))}
            placeholder={f.placeholder}
            hint={f.hint}
            required={!f.optional}
          />
        ),
      )}
      {choiceNote ? <Alert tone="warn">{choiceNote}</Alert> : null}
      <EnvChoice value={env} onChange={setEnv} />
      <Field label="Name" value={title} onChange={setTyped} placeholder={service.example} hint="As it shows in Ringkeeper. Change it if you like." required />
      {shared ? <Toggle label="Use in all projects" checked={all} onChange={setAll} hint="Every client's projects, new ones too." /> : null}
      {service.note ? <p className="text-sm text-muted">{service.note}</p> : null}
      {error ? <Alert>{error}</Alert> : null}
      <Button type="submit" kind="primary" busy={busy} disabled={!title.trim() || !env || !filled || (!shared && !owner.trim()) || (!!service.address && !service.address.optional && !address.trim())}>
        {project ? `Add and link to ${projectName(project)}` : "Add connection"}
      </Button>
    </form>
  );
}
