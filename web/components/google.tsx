"use client";

import Link from "@/components/link";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, Details, Field, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface Access {
  key: string;
  label: string;
  scope: string;
  note: string;
}

interface GoogleApp {
  configured: boolean;
  client_id: string | null;
  desktop: boolean;
  access: Access[];
}

interface GoogleTool {
  name: string;
  kind: "read" | "write";
  description: string;
  allowed: boolean;
  rule: "ask" | "run" | null;
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

const CONSOLE = "https://console.cloud.google.com";
const APIS: [string, string][] = [
  ["Gmail API", "gmail.googleapis.com"],
  ["Google Drive API", "drive.googleapis.com"],
  ["Google Calendar API", "calendar-json.googleapis.com"],
  ["Google Sheets API", "sheets.googleapis.com"],
];
export const DEFAULT_ACCESS = ["mail_read", "mail_send"];

function Outside({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline">
      {children}
    </a>
  );
}

/** Settings › Google: the owner's own Google app, set up once in their own Google Cloud
 * project. Every Google account (a client's or yours) signs in through it. */
export function GoogleSettings() {
  const app = useApi<GoogleApp>("google/app");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const data = app.data;
  if (!data) return null;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api("google/app", { method: "PUT", json: { client_id: clientId.trim(), client_secret: secret } });
      setSecret("");
      setClientId("");
      setSaved(true);
      app.reload();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    if (!window.confirm("Remove your Google app? Google accounts can't sign in again until you add one; the ones signed in keep working until their next refresh fails.")) return;
    await api("google/app", { method: "DELETE" }).catch((err: unknown) => setError(errorText(err)));
    app.reload();
  }

  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Google</h2>
      <p className="text-sm text-muted">
        Gmail, Drive, Calendar and Sheets, for your clients&apos; Google accounts and your own. They sign in through a Google app you create once, in your own Google Cloud project: free, and no one else&apos;s app sees the accounts.
      </p>
      {data.configured ? (
        <Alert tone="good">
          Your Google app is set up ({data.client_id}). Add Google accounts under Connections › Add a connection.
        </Alert>
      ) : null}
      <Details label={data.configured ? "How it was set up" : "Set it up (about 10 minutes)"}>
        <ol className="list-decimal space-y-3 pl-5 text-sm">
          <li>
            Open the <Outside href={`${CONSOLE}/projectcreate`}>Google Cloud console</Outside> signed in with your own Google account, and create a project (any name, say “Ringkeeper”). No billing is needed.
          </li>
          <li>
            Turn on the four APIs (each link opens it; click <strong>Enable</strong>):{" "}
            {APIS.map(([label, id], i) => (
              <span key={id}>
                {i ? ", " : ""}
                <Outside href={`${CONSOLE}/apis/library/${id}`}>{label}</Outside>
              </span>
            ))}
            .
          </li>
          <li>
            Open <Outside href={`${CONSOLE}/auth/branding`}>Google Auth Platform › Branding</Outside> and click <strong>Get started</strong>: app name “Ringkeeper”, your email as support email, <strong>Audience: External</strong>, your email as contact, agree, <strong>Create</strong>.
          </li>
          <li>
            Open <Outside href={`${CONSOLE}/auth/audience`}>Audience</Outside> and click <strong>Publish app</strong>, then <strong>Confirm</strong>. Left in “Testing”, Google signs every account out after 7 days.
          </li>
          <li>
            Open <Outside href={`${CONSOLE}/auth/scopes`}>Data Access</Outside> › <strong>Add or remove scopes</strong>, paste these into “Manually add scopes”, <strong>Add to table</strong>, <strong>Update</strong>, <strong>Save</strong>:
            <pre className="mt-1 overflow-x-auto rounded-lg bg-sunken p-2 text-xs">{data.access.map((a) => a.scope).join("\n")}</pre>
          </li>
          <li>
            Open <Outside href={`${CONSOLE}/auth/clients`}>Clients</Outside> › <strong>Create client</strong>: Application type <strong>Desktop app</strong>, name “Ringkeeper desktop”, <strong>Create</strong>. Copy the Client ID and the Client secret now: Google shows the secret only once.
          </li>
          <li>Paste both below. They&apos;re stored encrypted on this computer.</li>
        </ol>
        <div className="mt-3 space-y-1 rounded-xl bg-sunken p-3 text-xs text-muted">
          <p>
            <strong>What to expect:</strong> the app stays “unverified”. Each account you sign in sees “Google hasn&apos;t verified this app”: click <strong>Advanced</strong>, then <strong>Go to Ringkeeper (unsafe)</strong>. It&apos;s your own app, so that&apos;s expected.
          </p>
          <p>Google allows an unverified app up to 100 accounts over its whole life (Gmail, Drive, Calendar and Sheets access all count).</p>
          <p>A Google account stops working here if its password changes (for mail), if it&apos;s unused for six months, or if its owner removes Ringkeeper; sign in again then. A client&apos;s Workspace admin may need to trust your app&apos;s client ID.</p>
        </div>
      </Details>
      <Field label="Client ID" value={clientId} onChange={setClientId} placeholder="…apps.googleusercontent.com" />
      <Field label="Client secret" type="password" value={secret} onChange={setSecret} />
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!clientId.trim() || !secret} onClick={save}>
          {data.configured ? "Replace" : "Save"}
        </Button>
        {data.configured ? (
          <Button kind="ghost" onClick={remove}>
            Remove
          </Button>
        ) : null}
      </div>
      {saved ? <Alert tone="good">Saved.</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

/** Choose what a Google account may be used for, sign in on Google's page, and wait for
 * it to come back. A new account (a client's, or yours) or, with `replace`, the same one
 * again (more access, or Google stopped accepting it). */
export function GoogleConnect({
  owner,
  shared = false,
  project,
  replace,
  initial = DEFAULT_ACCESS,
  onAdded,
}: {
  owner?: string;
  shared?: boolean;
  project?: string;
  replace?: string;
  initial?: string[];
  onAdded: (name: string) => void;
}) {
  const app = useApi<GoogleApp>("google/app");
  const [access, setAccess] = useState<string[]>(initial);
  const [name, setName] = useState("");
  const [all, setAll] = useState(false);
  const [state, setState] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!state) return;
    const deadline = Date.now() + 10 * 60_000;
    const timer = setInterval(() => {
      api<{ state: string; name: string | null; error: string | null }>(`google/signin/${state}`)
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

  const data = app.data;
  if (!data) return null;
  if (!data.desktop) return <p className="text-sm text-muted">Google accounts work in the desktop app for now.</p>;
  if (!data.configured) {
    return (
      <p className="text-sm text-muted">
        First set up your Google app: <Link href="/settings#google" className="text-accent underline">Settings › Google</Link>.
      </p>
    );
  }

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const body = replace
        ? { access, replace }
        : { access, owner: shared ? undefined : owner, shared, all_projects: shared && all, name: name.trim() || undefined, project };
      const started = await api<{ url: string; state: string }>("google/signin", { json: body });
      window.open(started.url, "_blank", "noopener");
      setState(started.state);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  const toggle = (key: string, on: boolean) => setAccess((now) => (on ? [...now, key] : now.filter((k) => k !== key)));

  return (
    <div className="space-y-3">
      <fieldset className="space-y-1">
        <legend className="text-sm font-medium">What may it be used for?</legend>
        {data.access.map((a) => (
          <label key={a.key} className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={access.includes(a.key)} onChange={(e) => toggle(a.key, e.target.checked)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span>
              {a.label}
              {a.note ? <span className="block text-xs text-muted">{a.note}</span> : null}
            </span>
          </label>
        ))}
        <p className="text-xs text-muted">Reading runs; every write (sending, sharing, invites, edits, uploads, drafts, the trash) asks you first. You can let a tool run without asking later, per account.</p>
      </fieldset>
      {replace ? null : (
        <>
          <Field label="Name (optional)" value={name} onChange={setName} placeholder="from the address, like acme-info" />
          {shared ? <Toggle label="Use in all projects" checked={all} onChange={setAll} hint="Every client's projects, new ones too." /> : null}
        </>
      )}
      {state ? (
        <Alert tone="warn">Finish on Google&apos;s page (it opened in your browser). If it says Google hasn&apos;t verified the app: Advanced › Go to Ringkeeper (unsafe).</Alert>
      ) : (
        <Button kind="primary" busy={busy} disabled={!access.length} onClick={signIn}>
          {replace ? "Sign in again" : "Sign in with Google"}
        </Button>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** A Google account's card: which address, what it was given, and per write tool, ask
 * first (the default) or run without asking. */
export function GoogleAccount({ name, onChange }: { name: string; onChange: () => void }) {
  const account = useApi<{ email: string; access: string[]; tools: GoogleTool[] }>(`connections/${name}/google`);
  const app = useApi<GoogleApp>("google/app");
  const [changing, setChanging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const data = account.data;
  if (!data) return null;
  const labels = Object.fromEntries((app.data?.access ?? []).map((a) => [a.key, a.label]));

  async function rule(tool: string, run: boolean) {
    setError(null);
    const rules = Object.fromEntries(data!.tools.filter((t) => t.rule === "run" && t.name !== tool).map((t) => [t.name, "run"]));
    if (run) rules[tool] = "run";
    try {
      await api(`connections/${name}/write-rules`, { method: "PUT", json: { rules } });
      account.reload();
      onChange();
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <div className="space-y-2">
      <p className="text-sm">
        <span className="font-medium">{data.email}</span>
        <span className="text-muted"> · {data.access.map((k) => labels[k] ?? k).join(", ")}</span>
      </p>
      <Details label="Tools and approvals">
        <ul className="space-y-2">
          {data.tools.map((t) => (
            <li key={t.name} className="text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <code>{t.name}</code>
                {t.kind === "read" ? <Badge tone="good">Reads</Badge> : null}
                {!t.allowed ? <Badge>Not given</Badge> : null}
              </div>
              <p className="text-xs text-muted">{t.description}</p>
              {t.kind === "write" && t.allowed ? (
                <Toggle label="Run without asking" checked={t.rule === "run"} onChange={(on) => void rule(t.name, on)} hint={t.rule === "run" ? "Runs and is logged; you're not asked." : "Asks you first."} />
              ) : null}
            </li>
          ))}
        </ul>
      </Details>
      {changing ? (
        <GoogleConnect replace={name} initial={data.access} onAdded={() => { setChanging(false); account.reload(); onChange(); }} />
      ) : (
        <Button kind="ghost" onClick={() => setChanging(true)}>
          Change access or sign in again
        </Button>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
