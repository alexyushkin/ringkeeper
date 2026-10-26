"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ClaudeDesktopImport } from "@/components/claude-desktop-import";
import { ClaudeCodeFolders } from "@/components/claude-folders";
import { ConnectionForm, ProjectForm, TestButton, type ConnectionData, type ProjectSaved } from "@/components/forms";
import { TelegramPairing } from "@/components/telegram";
import { Alert, Button, Card, Details, Field, Loading, Select, Toggle } from "@/components/ui";
import { ApiError, api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { download } from "@/lib/client/words";

interface Status {
  owner: boolean;
  signed_in: boolean;
  setup_code_hint: string | null;
  // Only when signed in:
  recovery_saved?: boolean;
  claude?: { mode: string; api_key: boolean; subscription: boolean };
  telegram?: { token: boolean; enabled: boolean; chats: number[] };
  projects?: number;
  connections?: number;
  done?: boolean;
}

/* The desktop app has no login (the window is the key) and only an API key for
 * Claude; connections and Telegram are in the app once it's set up. */
const STEPS = DESKTOP ? ["Claude", "First project", "Connect a service", "Done"] : ["Your login", "Claude", "Telegram", "First project", "First connection", "Done"];

/** First run, no terminal: login, recovery key, Claude, Telegram (optional), a project, a connection. */
export default function SetupPage() {
  const router = useRouter();
  const [status, setStatus] = useState<Status | null>(null);
  const [step, setStep] = useState<number | null>(null);
  const [project, setProject] = useState<string | null>(null);
  const [saved, setSaved] = useState<ProjectSaved | null>(null);

  const refresh = useCallback(async () => {
    const next = await api<Status>("setup/status");
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    refresh()
      .then(async (s) => {
        if (DESKTOP) {
          const skipped = localStorage.getItem("rk-setup-connection") === "skipped";
          setStep(!s.claude?.api_key ? 0 : s.projects === 0 ? 1 : s.connections === 0 && !skipped ? 2 : 3);
          return;
        }
        if (s.owner && !s.signed_in) {
          // Setup was started: only a signed-in owner may continue it.
          router.replace("/login?next=/setup");
          return;
        }
        const skipped = localStorage.getItem("rk-setup-telegram") === "skipped";
        setStep(
          !s.owner || !s.recovery_saved ? 0
          : !s.claude?.api_key && !s.claude?.subscription ? 1
          : !s.telegram?.enabled && !skipped ? 2
          : s.projects === 0 ? 3
          : s.connections === 0 ? 4
          : 5,
        );
      })
      .catch(() => setStep(0));
  }, [refresh, router]);

  if (step === null || status === null) return <Loading />;

  const next = () => setStep((n) => Math.min((n ?? 0) + 1, STEPS.length - 1));

  return (
    <main className="mx-auto max-w-lg px-4 py-6">
      <div className="mb-2 flex items-center gap-2 text-lg font-semibold">
        <span aria-hidden className="inline-block size-5 rounded-full border-4 border-accent" />
        Set up Ringkeeper
      </div>
      <ol className="mb-6 flex gap-1" aria-label="Progress">
        {STEPS.map((name, index) => (
          <li key={name} className={`h-1.5 flex-1 rounded-full ${index <= step ? "bg-accent" : "bg-line"}`} title={name}>
            <span className="sr-only">
              {name}
              {index < step ? " (done)" : index === step ? " (now)" : ""}
            </span>
          </li>
        ))}
      </ol>
      <p className="mb-2 text-sm text-muted">
        Step {step + 1} of {STEPS.length}: {STEPS[step]}
      </p>
      {DESKTOP ? (
        <>
          {step === 0 ? <ClaudeStep onDone={next} /> : null}
          {step === 1 ? (
            <DesktopProjectsStep
              onDone={(name, result) => {
                setProject(name);
                setSaved(result ?? null);
                next();
              }}
            />
          ) : null}
          {step === 2 ? <DesktopConnectStep onDone={next} /> : null}
          {step === 3 ? <DesktopDone project={project} saved={saved} /> : null}
        </>
      ) : null}
      {!DESKTOP && step === 0 ? <OwnerStep status={status} onDone={() => refresh().then(next)} /> : null}
      {!DESKTOP && step === 1 ? <ClaudeStep onDone={next} /> : null}
      {!DESKTOP && step === 2 ? <TelegramStep onDone={next} /> : null}
      {!DESKTOP && step === 3 ? (
        <ProjectStep
          onSaved={(name) => {
            setProject(name);
            next();
          }}
        />
      ) : null}
      {!DESKTOP && step === 4 ? <ConnectionStep project={project} onDone={next} /> : null}
      {!DESKTOP && step === 5 ? (
        <Card className="space-y-4">
          <h2 className="text-lg font-semibold">You're set up</h2>
          <p className="text-sm">
            Ask for something in plain words, typed or spoken. Ringkeeper sends it to the right project, and anything that touches live client data waits for your approval.
          </p>
          <Link href="/" className="inline-flex min-h-11 items-center rounded-xl bg-accent px-4 text-sm font-medium text-accent-ink">
            Ask for something
          </Link>
        </Card>
      ) : null}
    </main>
  );
}

function ProjectStep({ onSaved }: { onSaved: (name: string, saved?: ProjectSaved) => void }) {
  return (
    <Card>
      <h2 className="mb-1 text-lg font-semibold">Add your first project</h2>
      <p className="mb-4 text-sm text-muted">A project is one piece of client work: a website, an app, a set of automations.</p>
      <ProjectForm onSaved={onSaved} />
    </Card>
  );
}

/** Desktop: projects from Claude Code's folders or by hand, and Claude Desktop's servers for them. */
function DesktopProjectsStep({ onDone }: { onDone: (name: string, saved?: ProjectSaved) => void }) {
  const projects = useApi<{ name: string }[]>("projects");
  const [last, setLast] = useState<{ name: string; saved?: ProjectSaved } | null>(null);
  const [form, setForm] = useState(0); // a fresh form after each project added by hand
  const names = (projects.data ?? []).map((p) => p.name);
  return (
    <div className="space-y-4">
      <ClaudeCodeFolders
        onAdded={(added) => {
          setLast({ name: added.join(", ") });
          projects.reload();
        }}
      />
      <ClaudeDesktopImport projects={names} quiet />
      <Card>
        <h2 className="mb-1 text-lg font-semibold">{names.length ? "Add another project" : "Add your first project"}</h2>
        <p className="mb-4 text-sm text-muted">A project is one piece of client work: a website, an app, a set of automations.</p>
        <ProjectForm
          key={form}
          onSaved={(name, saved) => {
            setLast({ name, saved });
            setForm((n) => n + 1);
            projects.reload();
          }}
        />
      </Card>
      {names.length ? (
        <Card className="space-y-3">
          <p className="text-sm">
            Your projects: <strong>{names.join(", ")}</strong>
          </p>
          <Button kind="primary" onClick={() => onDone(last?.name ?? names.join(", "), last?.saved)}>
            Continue
          </Button>
        </Card>
      ) : null}
    </div>
  );
}

/** Optional: one service for one project, so the first request can use it. */
function DesktopConnectStep({ onDone }: { onDone: () => void }) {
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const [chosen, setChosen] = useState("");
  const [added, setAdded] = useState<string | null>(null);
  const list = projects.data ?? [];
  const project = list.find((p) => p.name === chosen) ?? list[0];

  function skip() {
    localStorage.setItem("rk-setup-connection", "skipped");
    onDone();
  }

  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">Connect a service (optional)</h2>
      <p className="text-sm text-muted">
        Give a project access to a service it works with: its database, automations, Airtable, Vercel, Twilio or Gmail. Ringkeeper keeps the key; the assistants never see it. You can add more on each project's page.
      </p>
      {added ? (
        <div className="space-y-4">
          <Alert tone="good">Added {added}. Its secret is stored encrypted and won't be shown again.</Alert>
          <TestButton name={added} />
          <Button kind="primary" onClick={onDone}>
            Continue
          </Button>
        </div>
      ) : project ? (
        <>
          {list.length > 1 ? (
            <Select label="For project" value={project.name} onChange={setChosen} options={list.map((p): [string, string] => [p.name, p.name])} />
          ) : null}
          <ConnectionForm key={project.name} client={project.owner} project={project.name} onSaved={(c) => setAdded(c.name)} />
          <Button kind="ghost" onClick={skip}>
            Skip for now
          </Button>
        </>
      ) : (
        <Loading />
      )}
    </Card>
  );
}

/** The desktop app's last step: where the files are, and starting with Windows. */
function DesktopDone({ project, saved }: { project: string | null; saved: ProjectSaved | null }) {
  const router = useRouter();
  const settings = useApi<{ values: { start_at_login: boolean } }>("settings");
  const [choice, setChoice] = useState<boolean | null>(null);
  const [signIn, setSignIn] = useState("sign in");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => setSignIn(navigator.userAgent.includes("Windows") ? "sign in to Windows" : "log in"), []);
  const startAtLogin = choice ?? settings.data?.values.start_at_login ?? true;

  async function finish() {
    setBusy(true);
    setError(null);
    try {
      if (startAtLogin !== settings.data?.values.start_at_login) {
        await api("settings", { method: "PATCH", json: { start_at_login: startAtLogin } });
      }
      await api("setup/done", { method: "POST" });
      router.replace("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(false);
    }
  }

  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">You're set up</h2>
      {project && saved?.folder ? (
        <div className="space-y-1 text-sm">
          <p>
            {saved.created ? `Ringkeeper made a new folder for ${project}:` : `${project} uses this folder:`}{" "}
            <span className="font-mono text-xs break-all">{saved.folder}</span>
          </p>
          <p className="text-muted">
            {saved.git
              ? "Each task's changes go on a branch of their own, for you to review."
              : "It isn't a git repository, so tasks change its files directly and list what they changed."}
          </p>
        </div>
      ) : project ? (
        <p className="text-sm">Added {project}.</p>
      ) : null}
      <p className="text-sm">
        Ask for something in plain words. Ringkeeper sends it to the right project, and anything that touches live client data waits for your approval.
      </p>
      <Toggle
        label={`Start Ringkeeper when I ${signIn}`}
        checked={startAtLogin}
        onChange={setChoice}
        hint="It waits in the tray, so requests and approvals keep coming in. You can change this in Settings."
      />
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!settings.data} onClick={finish}>
        Start using Ringkeeper
      </Button>
    </Card>
  );
}

function OwnerStep({ status, onDone }: { status: Status; onDone: () => void }) {
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [created, setCreated] = useState(status.owner);
  const [saved, setSaved] = useState(false);
  const [downloaded, setDownloaded] = useState(false);

  async function create(event: React.FormEvent) {
    event.preventDefault();
    if (password !== again) {
      setError("The two passwords don't match.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await api("setup/owner", { json: { password, setup_code: code } });
      setCreated(true);
    } catch (err) {
      setError(
        err instanceof ApiError && err.status === 403
          ? "That setup code isn't right. Check it and try again."
          : err instanceof ApiError && err.status === 429
            ? "Too many tries. Wait 15 minutes and try again."
            : err instanceof Error
              ? err.message
              : String(err),
      );
    } finally {
      setBusy(false);
    }
  }

  async function getKey() {
    setError(null);
    try {
      const key = await api<{ filename: string; key: string; note: string }>("setup/recovery-key", { method: "POST" });
      download(key.filename, `Ringkeeper recovery key\n\n${key.key}\n\n${key.note}\nKeep this file somewhere safe, away from the server.\n`);
      setDownloaded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function finish() {
    await api("setup/recovery-key/saved", { method: "POST" });
    onDone();
  }

  if (!created) {
    return (
      <Card>
        <h2 className="mb-1 text-lg font-semibold">Create your login</h2>
        <p className="mb-4 text-sm text-muted">You're the only account. Choose a password you don't use anywhere else.</p>
        <form onSubmit={create} className="space-y-4">
          <Field
            label="Setup code"
            value={code}
            onChange={setCode}
            placeholder="ABCD-EFGH"
            hint={`This proves you installed Ringkeeper. Find it ${status.setup_code_hint ?? "in the server's log"}.`}
            autoComplete="one-time-code"
            required
          />
          <Field label="Password" type="password" value={password} onChange={setPassword} autoComplete="new-password" hint="At least 12 characters." required />
          <Field label="Password again" type="password" value={again} onChange={setAgain} autoComplete="new-password" required />
          {error ? <Alert>{error}</Alert> : null}
          <Button type="submit" kind="primary" busy={busy} disabled={!code || password.length < 12}>
            Create login
          </Button>
        </form>
      </Card>
    );
  }
  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">Save your recovery key</h2>
      <p className="text-sm">
        Ringkeeper keeps your connection secrets encrypted with this key. <strong>If you lose this file, you'll need to re-enter your connection secrets.</strong> Keep it somewhere safe, like a password manager, not on the server.
      </p>
      <Button onClick={getKey} kind={downloaded ? "secondary" : "primary"}>
        {downloaded ? "Download again" : "Download recovery key"}
      </Button>
      <Toggle label="I saved the recovery key somewhere safe" checked={saved} onChange={setSaved} />
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" onClick={finish} disabled={!saved || !downloaded}>
        Continue
      </Button>
    </Card>
  );
}

function ClaudeStep({ onDone }: { onDone: () => void }) {
  const [key, setKey] = useState("");
  const [mode, setMode] = useState<"api_key" | "subscription">("api_key");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ message: string }>("secrets/claude", { method: "PUT", json: { mode, value: key } });
      setKey("");
      setMessage(result.message);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <h2 className="mb-1 text-lg font-semibold">Connect Claude</h2>
      {DESKTOP ? (
        <p className="mb-4 text-sm text-muted">
          Ringkeeper's assistants run on Claude. Paste an API key from{" "}
          <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer" className="text-accent underline">
            console.anthropic.com
          </a>{" "}
          (Settings › API keys). It's checked with a free call, stored encrypted, and never shown again.
        </p>
      ) : (
        <p className="mb-4 text-sm text-muted">
          Ringkeeper's assistants run on Claude. Paste an API key from console.anthropic.com (Settings › API keys). It's checked, stored encrypted, and never shown again.
        </p>
      )}
      {message ? (
        <div className="space-y-4">
          <Alert tone="good">{message}</Alert>
          <Button kind="primary" onClick={onDone}>
            Continue
          </Button>
        </div>
      ) : (
        <form onSubmit={save} className="space-y-4" autoComplete="off">
          <Field
            label={mode === "api_key" ? "Anthropic API key" : "Claude subscription token"}
            type="password"
            value={key}
            onChange={setKey}
            placeholder={mode === "api_key" ? "sk-ant-api03-…" : "sk-ant-oat01-…"}
            required
          />
          {DESKTOP ? null : (
            <Details label="Advanced">
              <Toggle
                label="Use my Claude subscription instead"
                checked={mode === "subscription"}
                onChange={(on) => setMode(on ? "subscription" : "api_key")}
                hint="Personal use only, on your own account: run `claude setup-token` and paste the token it prints. For client work, use an API key."
              />
            </Details>
          )}
          {error ? <Alert>{error}</Alert> : null}
          <Button type="submit" kind="primary" busy={busy} disabled={!key}>
            {mode === "api_key" ? "Check and save" : "Save"}
          </Button>
        </form>
      )}
    </Card>
  );
}

function TelegramStep({ onDone }: { onDone: () => void }) {
  function skip() {
    localStorage.setItem("rk-setup-telegram", "skipped");
    onDone();
  }

  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">Telegram (optional)</h2>
      <p className="text-sm text-muted">
        Get approvals and questions on your phone. Telegram only gets short summaries; full details stay here. Create a bot with @BotFather in Telegram and paste its token.
      </p>
      <TelegramPairing onDone={onDone} onSkip={skip} />
    </Card>
  );
}

function ConnectionStep({ project: added, onDone }: { project: string | null; onDone: () => void }) {
  const [connection, setConnection] = useState<ConnectionData | null>(null);
  const [ok, setOk] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [project, setProject] = useState<string | null>(added);

  useEffect(() => {
    // Picked up again after a reload: use the project made in the previous step.
    if (project === null) {
      api<{ name: string }[]>("projects")
        .then((all) => setProject(all[0]?.name ?? null))
        .catch(() => undefined);
    }
  }, [project]);

  async function finish() {
    if (project && connection) {
      try {
        await api(`projects/${project}/connections/${connection.name}`, {
          method: "PUT",
          json: { is_default: connection.env !== "prod" },
        });
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
        return;
      }
    }
    onDone();
  }

  return (
    <Card className="space-y-4">
      <h2 className="text-lg font-semibold">Add your first connection</h2>
      <p className="text-sm text-muted">A connection is a service the project uses, like its database or its automations.</p>
      {connection ? (
        <div className="space-y-4">
          <Alert tone="good">Added {connection.name}. Its secret is stored encrypted and won't be shown again.</Alert>
          <TestButton name={connection.name} onDone={setOk} />
          {error ? <Alert>{error}</Alert> : null}
          <Button kind="primary" onClick={finish} disabled={!ok}>
            {project ? `Connect it to ${project} and finish` : "Finish"}
          </Button>
        </div>
      ) : (
        <ConnectionForm onSaved={setConnection} />
      )}
    </Card>
  );
}
