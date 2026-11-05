"use client";

import Link from "@/components/link";
import { useEffect, useState } from "react";

import { Alert, Badge, Button, Card, ConfirmButton, Details, Field, LinkButton, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { connectionHref } from "@/lib/client/routes";
import { clientName, projectName } from "@/lib/client/scope";

interface Access {
  key: string;
  label: string;
  scope: string;
  note: string;
}

interface AppInfo {
  id: string;
  name: string;
  client_id: string;
  accounts: number;
}

interface AccountInfo {
  name: string;
  email: string | null;
  owner: string;
  shared: boolean;
  app: string | null;
}

interface GoogleApp {
  configured: boolean;
  client_id: string | null;
  apps: AppInfo[];
  accounts: AccountInfo[];
  desktop: boolean;
  access: Access[];
}

interface Credentials {
  name?: string;
  client_id: string;
  client_secret: string;
}

interface GoogleTool {
  name: string;
  kind: "read" | "write";
  description: string;
  allowed: boolean;
  rule: "ask" | "run" | null;
  fixed: boolean;
}

const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

const CONSOLE = "https://console.cloud.google.com";
const APIS: [string, string][] = [
  ["Gmail API", "gmail.googleapis.com"],
  ["Google Drive API", "drive.googleapis.com"],
  ["Google Calendar API", "calendar-json.googleapis.com"],
  ["Google Sheets API", "sheets.googleapis.com"],
  ["Search Console API", "searchconsole.googleapis.com"],
  ["Analytics Data API", "analyticsdata.googleapis.com"],
  ["Analytics Admin API", "analyticsadmin.googleapis.com"],
];
export const DEFAULT_ACCESS = ["mail_read", "mail_send", "mail_drafts"];
// Ringkeeper's pages, for the Branding page of your Google app.
const HOMEPAGE = "https://yushkin.us/ringkeeper";
const PRIVACY = "https://yushkin.us/ringkeeper/privacy";
// Google's limit for an unverified app, over its whole life.
const ACCOUNT_CAP = 100;

/** Text to paste into Google's form, in a monospace box. */
function Copyable({ text }: { text: string }) {
  return <code className="rounded bg-sunken px-1 py-0.5 text-xs select-all">{text}</code>;
}

function Outside({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-accent underline">
      {children}
    </a>
  );
}

/** How to create a Google app in a Google Cloud project, and what to expect of it. */
function SetupSteps({ access }: { access: Access[] }) {
  return (
    <>
      <ol className="list-decimal space-y-3 pl-5 text-sm">
        <li>
          Open the <Outside href={`${CONSOLE}/projectcreate`}>Google Cloud console</Outside> signed in with your own Google account, and create a project (any name, say “Ringkeeper”). No billing is needed.
        </li>
        <li>
          Turn on these APIs (each link opens it; click <strong>Enable</strong>):{" "}
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
          Back in <Outside href={`${CONSOLE}/auth/branding`}>Branding</Outside>, fill in what Google needs before it publishes the app, then <strong>Save</strong>:
          <ul className="mt-1 list-disc space-y-1 pl-5">
            <li>App name: <Copyable text="Ringkeeper" />; User support email: yours</li>
            <li>
              Application home page: <Copyable text={HOMEPAGE} />
            </li>
            <li>
              Application privacy policy link: <Copyable text={PRIVACY} />
            </li>
            <li>
              Authorized domains: <strong>Add domain</strong>, <Copyable text="yushkin.us" />
            </li>
            <li>Developer contact information: your email</li>
          </ul>
        </li>
        <li>
          Open <Outside href={`${CONSOLE}/auth/audience`}>Audience</Outside> and click <strong>Publish app</strong>, then <strong>Confirm</strong>. Left in “Testing”, Google signs every account out after 7 days.
        </li>
        <li>
          Open <Outside href={`${CONSOLE}/auth/scopes`}>Data Access</Outside> › <strong>Add or remove scopes</strong>, paste these into “Manually add scopes”, <strong>Add to table</strong>, <strong>Update</strong>, <strong>Save</strong>:
          <pre className="mt-1 overflow-x-auto rounded-lg bg-sunken p-2 text-xs">{access.map((a) => a.scope).join("\n")}</pre>
        </li>
        <li>
          Open <Outside href={`${CONSOLE}/auth/clients`}>Clients</Outside> › <strong>Create client</strong>: Application type <strong>Desktop app</strong>, name “Ringkeeper desktop”, <strong>Create</strong>. Copy the Client ID and the Client secret now: Google shows the secret only once.
        </li>
        <li>Paste both here. They&apos;re stored encrypted on this computer.</li>
      </ol>
      <div className="mt-3 space-y-1 rounded-xl bg-sunken p-3 text-xs text-muted">
        <p>
          <strong>What to expect:</strong> the app stays “unverified”. Each account you sign in sees “Google hasn&apos;t verified this app”: click <strong>Advanced</strong>, then <strong>Go to Ringkeeper (unsafe)</strong>. It&apos;s your own app, so that&apos;s expected.
        </p>
        <p>Google allows an unverified app up to {ACCOUNT_CAP} accounts over its whole life (Gmail, Drive, Calendar and Sheets access all count).</p>
        <p>A Google account stops working here if its password changes (for mail), if it&apos;s unused for six months, or if its owner removes Ringkeeper; sign in again then. A client&apos;s Workspace admin may need to trust your app&apos;s client ID, or have you use an app in their own Google Cloud project.</p>
      </div>
    </>
  );
}

/** A Google app's client ID and secret (and, for another app, its name). */
function AppCredentials({ named = false, saveLabel, onSave, onCancel }: { named?: boolean; saveLabel: string; onSave: (json: Credentials) => Promise<unknown>; onCancel?: () => void }) {
  const [name, setName] = useState("");
  const [clientId, setClientId] = useState("");
  const [secret, setSecret] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await onSave({ name: named ? name.trim() : undefined, client_id: clientId.trim(), client_secret: secret });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      {named ? <Field label="Name" value={name} onChange={setName} placeholder="General Informatics' Google Cloud" hint="Whose Google Cloud project it's in, say." /> : null}
      <Field label="Client ID" value={clientId} onChange={setClientId} placeholder="…apps.googleusercontent.com" />
      <Field label="Client secret" type="password" value={secret} onChange={setSecret} />
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!clientId.trim() || !secret || (named && !name.trim())} onClick={save}>
          {saveLabel}
        </Button>
        {onCancel ? (
          <Button kind="ghost" onClick={onCancel}>
            Cancel
          </Button>
        ) : null}
      </div>
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Settings › Google app: the owner's own Google app, set up once in their own Google
 * Cloud project; every Google account (a client's or yours) signs in through it. There
 * can be more than one (a client's own project, or past Google's 100 accounts). */
export function GoogleSettings() {
  const app = useApi<GoogleApp>("google/app");
  const connections = useApi<{ name: string; title?: string | null; projects: string[]; last_test_ok: boolean | null; last_test_message: string | null }[]>("connections");
  const [replacing, setReplacing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const data = app.data;
  if (!data) return null;
  const several = data.apps.length > 1;
  const appName = (id: string | null) => data.apps.find((a) => a.id === id)?.name ?? id ?? "";

  async function saved(text: string) {
    setReplacing(null);
    setError(null);
    setNotice(text);
    app.reload();
  }
  async function remove(id: string) {
    setNotice(null);
    try {
      await api(`google/apps/${id}`, { method: "DELETE" });
      await saved("Removed.");
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Google app (one-time setup)</h2>
      <p className="text-sm">This connects Ringkeeper to Google once. Then add each Google account, as many as you need: one per client, or your own.</p>
      <p className="text-sm text-muted">
        Gmail, Drive, Calendar and Sheets, for your clients&apos; Google accounts and your own. They sign in through a Google app you create in your own Google Cloud project: free, and no one else&apos;s app sees the accounts.
      </p>
      {data.configured ? (
        <>
          <ul className="space-y-2">
            {data.apps.map((a) => (
              <li key={a.id} className="space-y-2 rounded-xl border border-line p-3">
                <div className="flex flex-wrap items-center gap-x-2 text-sm">
                  <span className="font-medium">{a.name}</span>
                  {several && a.id === data.apps[0]?.id ? <Badge tone="accent">default</Badge> : null}
                  <span className="min-w-0 truncate text-xs text-muted">{a.client_id}</span>
                  <span className="ml-auto text-xs text-muted">
                    {a.accounts} of {ACCOUNT_CAP} accounts
                  </span>
                </div>
                {replacing === a.id ? (
                  <AppCredentials saveLabel="Replace" onCancel={() => setReplacing(null)} onSave={(json) => api(`google/apps/${a.id}`, { method: "PUT", json }).then(() => saved("Saved."))} />
                ) : (
                  <div className="flex flex-wrap gap-2">
                    <Button kind="ghost" onClick={() => setReplacing(a.id)}>
                      Replace its client ID and secret
                    </Button>
                    <ConfirmButton
                      kind="secondary"
                      question={
                        a.accounts
                          ? `Remove ${a.name}? ${a.accounts === 1 ? "The account" : `The ${a.accounts} accounts`} that signed in through it stop working when their access next renews (within the hour); sign ${a.accounts === 1 ? "it" : "them"} in again through another app.`
                          : `Remove ${a.name}?`
                      }
                      confirm={`Remove ${a.name}`}
                      onConfirm={() => remove(a.id)}
                    >
                      Remove
                    </ConfirmButton>
                  </div>
                )}
              </li>
            ))}
          </ul>
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="text-sm font-medium">Google accounts ({data.accounts.length})</h3>
              <span className="ml-auto" />
              <LinkButton kind="primary" href="/connections/new?google=1">
                Add a Google account
              </LinkButton>
            </div>
            {data.accounts.length ? (
              <ul className="space-y-2">
                {data.accounts.map((a) => {
                  const found = connections.data?.find((c) => c.name === a.name);
                  return (
                    <li key={a.name} className="rounded-xl border border-line p-3 text-sm">
                      <Link href={connectionHref(a)} className="font-medium text-accent">
                        {a.email ?? found?.title ?? a.name}
                      </Link>
                      <span className="text-muted">
                        {" "}
                        · {a.shared ? "your own" : clientName(a.owner)}
                        {several ? ` · through ${appName(a.app)}` : ""}
                      </span>
                      <span className="block text-xs text-muted">
                        {found?.projects.length ? `Used by ${found.projects.map(projectName).join(", ")}` : "Not linked to a project yet"}
                      </span>
                      {found?.last_test_ok === false && found.last_test_message ? <span className="block text-xs text-bad">Last test failed: {found.last_test_message}</span> : null}
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="text-sm text-muted">No Google accounts yet. Add the first with Add a Google account.</p>
            )}
          </div>
          <Details label="Add another Google app">
            <div className="space-y-3">
              <p className="text-sm text-muted">
                For a client whose Workspace admin only trusts an app in their own Google Cloud project, or for more accounts than Google allows one app ({ACCOUNT_CAP}). Create it the same way (the steps below), in that project. Each Google account then picks the app it signs in through; the first is the default.
              </p>
              <AppCredentials key={data.apps.length} named saveLabel="Add app" onSave={(json) => api("google/apps", { json }).then(() => saved("Added."))} />
            </div>
          </Details>
        </>
      ) : null}
      <Details label={data.configured ? "How a Google app is set up" : "Set it up (about 10 minutes)"}>
        <SetupSteps access={data.access} />
      </Details>
      {data.configured ? null : <AppCredentials saveLabel="Save" onSave={(json) => api("google/app", { method: "PUT", json }).then(() => saved("Saved. Now add your Google accounts."))} />}
      {data.configured ? null : <p className="text-xs text-muted">Once it&apos;s saved, Add a Google account shows here.</p>}
      {notice ? <Alert tone="good">{notice}</Alert> : null}
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
  initialApp = "",
  onAdded,
}: {
  owner?: string;
  shared?: boolean;
  project?: string;
  replace?: string;
  initial?: string[];
  initialApp?: string;
  onAdded: (name: string) => void;
}) {
  const app = useApi<GoogleApp>("google/app");
  const [access, setAccess] = useState<string[]>(initial);
  const [appId, setAppId] = useState(initialApp);
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
        First set up your Google app: <Link href="/settings#google" className="text-accent underline">Settings › Google app</Link>.
      </p>
    );
  }

  async function signIn() {
    setBusy(true);
    setError(null);
    try {
      const through = appId || undefined; // left out: the first app, or the one it used
      const body = replace
        ? { access, replace, app: through }
        : { access, owner: shared ? undefined : owner, shared, all_projects: shared && all, name: name.trim() || undefined, project, app: through };
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
      {data.apps.length > 1 ? (
        <Select
          label="Sign in through"
          value={appId || (replace ? "" : (data.apps[0]?.id ?? ""))}
          onChange={setAppId}
          options={[...(replace && !initialApp ? [["", "The app it signed in through"] as [string, string]] : []), ...data.apps.map((a): [string, string] => [a.id, `${a.name} (${a.accounts} of ${ACCOUNT_CAP} accounts)`])]}
          hint="Which of your Google apps. A client's Workspace admin may trust only the one in their own Google Cloud project."
        />
      ) : null}
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
  const account = useApi<{ email: string; access: string[]; app: string | null; tools: GoogleTool[] }>(`connections/${name}/google`);
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
        {(app.data?.apps.length ?? 0) > 1 ? <span className="text-muted"> · through {app.data?.apps.find((a) => a.id === data.app)?.name ?? data.app}</span> : null}
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
              {t.kind === "write" && t.allowed && t.fixed ? <p className="text-xs text-muted">Always asks you first. More than 10 people at once needs the name typed.</p> : null}
              {t.kind === "write" && t.allowed && !t.fixed ? (
                <Toggle label="Run without asking" checked={t.rule === "run"} onChange={(on) => void rule(t.name, on)} hint={t.rule === "run" ? "Runs and is logged; you're not asked." : "Asks you first."} />
              ) : null}
            </li>
          ))}
        </ul>
      </Details>
      {changing ? (
        <GoogleConnect replace={name} initial={data.access} initialApp={data.app ?? ""} onAdded={() => { setChanging(false); account.reload(); onChange(); }} />
      ) : (
        <Button kind="ghost" onClick={() => setChanging(true)}>
          Change access or sign in again
        </Button>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
