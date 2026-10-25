"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

import { ConnectionForm, ProjectForm, TestButton, type ConnectionData } from "@/components/forms";
import { Alert, Button, Card, Details, Field, Loading, Toggle } from "@/components/ui";
import { ApiError, api } from "@/lib/client/api";
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
}

const STEPS = ["Your login", "Claude", "Telegram", "First project", "First connection", "Done"];

/** First run, no terminal: login, recovery key, Claude, Telegram (optional), a project, a connection. */
export default function SetupPage() {
  const router = useRouter();
  const [status, setStatus] = useState<Status | null>(null);
  const [step, setStep] = useState<number | null>(null);
  const [project, setProject] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const next = await api<Status>("setup/status");
    setStatus(next);
    return next;
  }, []);

  useEffect(() => {
    refresh()
      .then(async (s) => {
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
      {step === 0 ? <OwnerStep status={status} onDone={() => refresh().then(next)} /> : null}
      {step === 1 ? <ClaudeStep onDone={next} /> : null}
      {step === 2 ? <TelegramStep onDone={next} /> : null}
      {step === 3 ? (
        <Card>
          <h2 className="mb-1 text-lg font-semibold">Add your first project</h2>
          <p className="mb-4 text-sm text-muted">A project is one piece of client work: a website, an app, a set of automations.</p>
          <ProjectForm
            onSaved={(name) => {
              setProject(name);
              next();
            }}
          />
        </Card>
      ) : null}
      {step === 4 ? <ConnectionStep project={project} onDone={next} /> : null}
      {step === 5 ? (
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
      <p className="mb-4 text-sm text-muted">
        Ringkeeper's assistants run on Claude. Paste an API key from console.anthropic.com (Settings › API keys). It's checked, stored encrypted, and never shown again.
      </p>
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
          <Details label="Advanced">
            <Toggle
              label="Use my Claude subscription instead"
              checked={mode === "subscription"}
              onChange={(on) => setMode(on ? "subscription" : "api_key")}
              hint="Personal use only, on your own account: run `claude setup-token` and paste the token it prints. For client work, use an API key."
            />
          </Details>
          {error ? <Alert>{error}</Alert> : null}
          <Button type="submit" kind="primary" busy={busy} disabled={!key}>
            {mode === "api_key" ? "Check and save" : "Save"}
          </Button>
        </form>
      )}
    </Card>
  );
}

interface Chat {
  id: number;
  type: string;
  name: string;
  username: string | null;
}

function TelegramStep({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [bot, setBot] = useState<string | null>(null);
  const [chats, setChats] = useState<Chat[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Anyone can message a bot: only the chat that sends this code is offered.
  const [code] = useState(() => {
    const alphabet = "ABCDEFGHJKMNPQRSTUVWXYZ23456789";
    return Array.from(crypto.getRandomValues(new Uint8Array(6)), (n) => alphabet[n % alphabet.length]).join("");
  });

  useEffect(() => {
    if (!bot) return;
    const timer = setInterval(() => {
      api<{ chats: Chat[] }>("telegram/detect", { query: { code } })
        .then((found) => setChats(found.chats.filter((c) => c.type === "private")))
        .catch(() => undefined);
    }, 3000);
    return () => clearInterval(timer);
  }, [bot, code]);

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ bot: string }>("secrets/telegram", { method: "PUT", json: { token } });
      setToken("");
      setBot(result.bot);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function choose(chat: Chat) {
    await api("settings", { method: "PATCH", json: { telegram: true, telegram_chat_ids: [chat.id] } });
    onDone();
  }

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
      {bot ? (
        <div className="space-y-3">
          <Alert tone="good">Connected to {bot}.</Alert>
          <p className="text-sm">
            Now open Telegram and send <strong>{bot}</strong> this code: <strong className="font-mono text-base">{code}</strong>. Your chat shows up here.
          </p>
          {chats.length === 0 ? (
            <p className="text-sm text-muted">Waiting for your message…</p>
          ) : (
            chats.map((chat) => (
              <Button key={chat.id} kind="primary" onClick={() => choose(chat)} className="w-full">
                Use {chat.name}
                {chat.username ? ` (@${chat.username})` : ""}
              </Button>
            ))
          )}
        </div>
      ) : (
        <form onSubmit={save} className="space-y-4" autoComplete="off">
          <Field label="Bot token" type="password" value={token} onChange={setToken} placeholder="123456:ABC…" required />
          {error ? <Alert>{error}</Alert> : null}
          <div className="flex gap-2">
            <Button type="submit" kind="primary" busy={busy} disabled={!token}>
              Connect
            </Button>
            <Button kind="ghost" onClick={skip}>
              Skip for now
            </Button>
          </div>
        </form>
      )}
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
