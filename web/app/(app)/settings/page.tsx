"use client";

import { browserSupportsWebAuthn, startRegistration } from "@simplewebauthn/browser";
import { useEffect, useState } from "react";

import { ClaudeDesktopImport } from "@/components/claude-desktop-import";
import { ModelSettings, MoneySetting } from "@/components/model-settings";
import { TelegramPairing } from "@/components/telegram";
import { Alert, Button, Card, Details, Field, Loading, PageHeader, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";
import { AboutCard } from "@/components/about";
import { VoiceSettings } from "@/components/voice";
import { SETUP_TOKEN_HINT, WORKSPACE_HINT, download } from "@/lib/client/words";
import { ClaudeCodeLogin } from "@/components/claude-code-login";

interface Settings {
  values: {
    claude_auth_mode: string;
    allow_subscription: boolean;
    anthropic_workspace_id: string | null;
    telegram: boolean;
    telegram_chat_ids: number[];
    telegram_requests: boolean;
    telegram_summary_chars: number;
    telegram_question_chars: number;
    model_router: string;
    model_architect: string;
    model_worker: string;
    model_low: string;
    model_medium: string;
    model_high: string;
    effort_router: string;
    effort_architect: string;
    effort_worker: string;
    default_max_turns: number;
    task_budget_usd: number;
    max_concurrent_workers: number;
    retention_days: number;
    paused: boolean;
    start_at_login: boolean;
  };
  overridden: string[];
  services: Record<string, { running: boolean; error: string | null }>;
  claude: { mode: string; api_key: boolean; subscription: boolean; subscription_allowed: boolean; subscription_note: string };
  telegram: { token: boolean };
}


export default function SettingsPage() {
  const settings = useApi<Settings>("settings");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function change(values: Record<string, unknown>, done = "Saved.") {
    setError(null);
    setNotice(null);
    try {
      await api("settings", { method: "PATCH", json: values });
      setNotice(done);
      settings.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function stopEverything() {
    if (!window.confirm("Stop every running task and pause everything until you resume?")) return;
    try {
      const result = await api<{ stopped: number }>("stop-all", { method: "POST" });
      setNotice(`Stopped ${result.stopped} task${result.stopped === 1 ? "" : "s"}. Nothing new starts until you resume.`);
      settings.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  if (settings.error) return <Alert>{settings.error}</Alert>;
  if (!settings.data) return <Loading />;
  const v = settings.data.values;
  const telegramState = settings.data.services.telegram;

  return (
    <>
      <PageHeader title="Settings" />
      {notice ? <div className="mb-3"><Alert tone="good">{notice}</Alert></div> : null}
      {error ? <div className="mb-3"><Alert>{error}</Alert></div> : null}

      <Card className="mb-4 space-y-3 border-bad">
        <h2 className="font-semibold">Stop everything</h2>
        <p className="text-sm text-muted">Stops every running task and pauses new work until you resume. Approvals wait where they are.</p>
        {v.paused ? (
          <Button kind="primary" onClick={() => change({ paused: false }, "Resumed.")}>
            Resume
          </Button>
        ) : (
          <Button kind="danger" onClick={stopEverything} className="w-full sm:w-auto">
            Stop everything
          </Button>
        )}
      </Card>

      {DESKTOP ? <ThisComputer startAtLogin={v.start_at_login} onChange={(on) => change({ start_at_login: on })} /> : null}
      {DESKTOP ? <VoiceSettings /> : null}
      {DESKTOP ? <ImportFromClaudeDesktop /> : null}

      <ClaudeCard settings={settings.data} onSaved={settings.reload} />
      <PersonalMode on={v.allow_subscription} onChange={(on) => change({ allow_subscription: on })} />

      <Card className="mb-4 space-y-4">
        <h2 className="font-semibold">Telegram</h2>
        {DESKTOP && !settings.data.telegram.token ? (
          <>
            <p className="text-sm text-muted">
              Get approvals and questions on your phone while this computer is on. Telegram only gets short summaries; full details stay here. Create a bot with @BotFather in Telegram and paste its token.
            </p>
            <TelegramPairing onDone={settings.reload} />
          </>
        ) : (
          <>
            {!settings.data.telegram.token ? (
              <p className="text-sm text-muted">Not connected. Run the setup's Telegram step, or store a bot token below.</p>
            ) : null}
            <TelegramToken onSaved={settings.reload} />
          </>
        )}
        <Toggle label="Send approvals and questions to Telegram" checked={v.telegram} onChange={(on) => change({ telegram: on })} hint={telegramState?.error ? `Not running: ${telegramState.error}` : telegramState?.running ? "Running." : undefined} />
        <Toggle
          label="Accept new requests from Telegram"
          checked={v.telegram_requests}
          onChange={(on) => change({ telegram_requests: on })}
          hint="Off by default. When on, your client requests, typed or spoken, pass through Telegram's servers, which aren't end-to-end encrypted. Leave it off for anything you wouldn't put in a normal Telegram chat."
        />
        <Details>
          <div className="space-y-3">
            <TextSetting
              label="Allowed chat IDs (comma-separated)"
              value={v.telegram_chat_ids.join(", ")}
              onSave={(text) =>
                change({ telegram_chat_ids: text.split(",").map((s) => Number(s.trim())).filter((n) => Number.isInteger(n) && n !== 0) })
              }
            />
            <p className="text-xs text-muted">Only these chats get messages; everyone else gets nothing.</p>
            <NumberSetting label="Summary length (approvals, plans)" value={v.telegram_summary_chars} onSave={(n) => change({ telegram_summary_chars: n })} />
            <NumberSetting label="Question length" value={v.telegram_question_chars} onSave={(n) => change({ telegram_question_chars: n })} />
          </div>
        </Details>
      </Card>

      <Card className="mb-4 space-y-4">
        <h2 className="font-semibold">Work</h2>
        <NumberSetting label="Tasks at once" value={v.max_concurrent_workers} onSave={(n) => change({ max_concurrent_workers: n })} hint="How many tasks can run at the same time." />
        <NumberSetting label="Keep full transcripts for (days)" value={v.retention_days} onSave={(n) => change({ retention_days: n })} hint="After this, transcripts are deleted. Task summaries, approvals and the activity log stay." />
        <NumberSetting label="Max steps per task" value={v.default_max_turns} onSave={(n) => change({ default_max_turns: n })} hint="A task stops after this many agent turns." />
        {v.claude_auth_mode === "api_key" ? (
          <MoneySetting label="Spending cap per task ($)" value={v.task_budget_usd} onSave={(n) => change({ task_budget_usd: n })} hint="At the cap a task pauses and asks whether to give it this much more." />
        ) : null}
      </Card>

      <Card className="mb-4 space-y-3">
        <h2 className="font-semibold">Models and effort</h2>
        <ModelSettings values={v} onChange={(changes) => change(changes)} />
      </Card>

      {DESKTOP ? null : (
        <>
          <Passkeys />
          <RecoveryKey />
          <SignOut />
        </>
      )}
      <AboutCard />
    </>
  );
}

function NumberSetting({ label, value, onSave, hint }: { label: string; value: number; onSave: (n: number) => void; hint?: string }) {
  const [text, setText] = useState(String(value));
  useEffect(() => setText(String(value)), [value]);
  return (
    <div className="flex items-end gap-2">
      <div className="flex-1">
        <Field label={label} value={text} onChange={setText} inputMode="numeric" hint={hint} />
      </div>
      <Button disabled={text === String(value) || !/^\d+$/.test(text)} onClick={() => onSave(Number(text))}>
        Save
      </Button>
    </div>
  );
}

function TextSetting({ label, value, onSave }: { label: string; value: string; onSave: (text: string) => void }) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  return (
    <div className="flex items-end gap-2">
      <div className="flex-1">
        <Field label={label} value={text} onChange={setText} />
      </div>
      <Button disabled={text === value || !text.trim()} onClick={() => onSave(text.trim())}>
        Save
      </Button>
    </div>
  );
}

function ClaudeCard({ settings, onSaved }: { settings: Settings; onSaved: () => void }) {
  const allowed = settings.claude.subscription_allowed;
  // Without personal subscription mode, only an API key can be set here.
  const [mode, setMode] = useState(allowed ? settings.claude.mode : "api_key");
  const [key, setKey] = useState("");
  const savedWorkspace = settings.values.anthropic_workspace_id ?? "";
  const [workspace, setWorkspace] = useState(savedWorkspace);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    setError(null);
    try {
      if (key) {
        const json = { mode, value: key, workspace_id: workspace.trim() };
        const result = await api<{ message: string }>("secrets/claude", { method: "PUT", json });
        setKey("");
        setMessage(result.message);
      } else {
        // Only the workspace changed: it's sent with the saved key from the next request.
        await api("settings", { method: "PATCH", json: { anthropic_workspace_id: workspace.trim() || null } });
        setMessage("Saved the workspace.");
      }
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  async function useLogin() {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ message: string }>("secrets/claude/from-claude-code", { method: "POST" });
      setMessage(result.message);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }
  const stored = mode === "api_key" ? settings.claude.api_key : settings.claude.subscription;
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Claude</h2>
      {!allowed && settings.claude.mode === "subscription" ? (
        <Alert tone="warn">
          {DESKTOP
            ? "This install runs on your Claude subscription. To change that here, turn on personal subscription mode (open Settings#personal)."
            : "This install runs on your Claude subscription. To change that here, set RINGKEEPER_ALLOW_SUBSCRIPTION=1 in .env and restart."}
        </Alert>
      ) : null}
      {!allowed ? null : (
        <Select
          label="How Ringkeeper pays for Claude"
          value={mode}
          onChange={setMode}
          options={[
            ["api_key", "API key (for client work)"],
            ["subscription", "My Claude subscription (personal use on my own account)"],
          ]}
        />
      )}
      {mode === "subscription" ? <p className="text-sm text-muted">{settings.claude.subscription_note}</p> : null}
      <p className="text-sm text-muted">{stored ? "A key is saved (hidden)." : "No key saved for this option yet."}</p>
      <Field
        label={mode === "subscription" ? "Token from `claude setup-token`" : stored ? "Replace the key" : "Key"}
        type="password"
        value={key}
        onChange={setKey}
        hint={mode === "subscription" ? SETUP_TOKEN_HINT : undefined}
      />
      {mode === "api_key" ? (
        <Field label="Workspace ID (if your key needs one)" value={workspace} onChange={setWorkspace} placeholder="wrkspc_…" hint={WORKSPACE_HINT} />
      ) : null}
      <Button kind="primary" busy={busy} disabled={!key && workspace.trim() === savedWorkspace} onClick={save}>
        {key ? "Check and save" : "Save"}
      </Button>
      {mode === "subscription" && DESKTOP ? <ClaudeCodeLogin busy={busy} onClick={useLogin} /> : null}
      {message ? <Alert tone="good">{message}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

function ThisComputer({ startAtLogin, onChange }: { startAtLogin: boolean; onChange: (on: boolean) => void }) {
  const [signIn, setSignIn] = useState("sign in");
  useEffect(() => setSignIn(navigator.userAgent.includes("Windows") ? "sign in to Windows" : "log in"), []);
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">This computer</h2>
      <Toggle
        label={`Start Ringkeeper when I ${signIn}`}
        checked={startAtLogin}
        onChange={onChange}
        hint="It waits in the tray, so requests and approvals keep coming in. Closing the window keeps it running; to stop it, right-click the tray icon and choose Quit."
      />
    </Card>
  );
}

function ImportFromClaudeDesktop() {
  const projects = useApi<{ name: string }[]>("projects");
  return (
    <div className="mb-4">
      <ClaudeDesktopImport projects={(projects.data ?? []).map((p) => p.name)} />
    </div>
  );
}

function TelegramToken({ onSaved }: { onSaved: () => void }) {
  const [token, setToken] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function save() {
    setError(null);
    try {
      const saved = await api<{ bot: string }>("secrets/telegram", { method: "PUT", json: { token } });
      setToken("");
      setResult(`Connected to ${saved.bot}.`);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Details label="Change the bot">
      <div className="space-y-2">
        <Field label="Bot token" type="password" value={token} onChange={setToken} />
        <Button disabled={!token} onClick={save}>
          Save
        </Button>
        {result ? <Alert tone="good">{result}</Alert> : null}
        {error ? <Alert>{error}</Alert> : null}
      </div>
    </Details>
  );
}

function Passkeys() {
  const passkeys = useApi<{ id: string; name: string; created_at: string; last_used_at: string | null }[]>("auth/passkeys");
  const [error, setError] = useState<string | null>(null);
  const [supported, setSupported] = useState(false);
  useEffect(() => setSupported(browserSupportsWebAuthn() && window.isSecureContext), []);

  async function add() {
    setError(null);
    try {
      const { challenge_id, options } = await api<{ challenge_id: string; options: Parameters<typeof startRegistration>[0]["optionsJSON"] }>("auth/passkeys/register/options", { method: "POST" });
      const credential = await startRegistration({ optionsJSON: options });
      await api("auth/passkeys/register", { json: { challenge_id, credential, name: navigator.platform || "Passkey" } });
      passkeys.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Passkeys</h2>
      <p className="text-sm text-muted">Sign in with your fingerprint, face or device PIN instead of the password.</p>
      <ul className="space-y-1 text-sm">
        {passkeys.data?.map((p) => (
          <li key={p.id} className="flex items-center gap-2">
            {p.name}
            <Button kind="ghost" onClick={() => api(`auth/passkeys/${p.id}`, { method: "DELETE" }).then(passkeys.reload)}>
              Remove
            </Button>
          </li>
        ))}
      </ul>
      {supported ? <Button onClick={add}>Add a passkey</Button> : <p className="text-sm text-muted">Passkeys need the app's https:// address.</p>}
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}

function RecoveryKey() {
  const [error, setError] = useState<string | null>(null);
  async function get() {
    setError(null);
    try {
      const key = await api<{ filename: string; key: string; note: string }>("setup/recovery-key", { method: "POST" });
      download(key.filename, `Ringkeeper recovery key\n\n${key.key}\n\n${key.note}\n`);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Recovery key</h2>
      <p className="text-sm text-muted">If you lose this file, you'll need to re-enter your connection secrets.</p>
      <Details label="Download it again">
        <div className="space-y-2">
          <p className="text-sm text-muted">You may be asked for your password first.</p>
          <Button onClick={get}>Download</Button>
          {error ? <Alert>{error}</Alert> : null}
        </div>
      </Details>
    </Card>
  );
}

function SignOut() {
  return (
    <Button
      onClick={async () => {
        await api("auth/logout", { method: "POST" });
        window.location.href = "/login";
      }}
    >
      Sign out
    </Button>
  );
}


/** Hidden unless the page is opened at #personal: your own install may use your own
 * Claude subscription. */
function PersonalMode({ on, onChange }: { on: boolean; onChange: (on: boolean) => void }) {
  const [shown, setShown] = useState(false);
  useEffect(() => setShown(window.location.hash === "#personal"), []);
  if (!shown) return null;
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Personal subscription mode</h2>
      <Toggle
        label="Let this install use my own Claude subscription"
        checked={on}
        onChange={onChange}
        hint="Personal use only, on your own install. Then choose it under Claude above."
      />
    </Card>
  );
}
