"use client";

import { useState } from "react";

import { Changes } from "@/components/changes";
import { Alert, Badge, Button, Card, ConfirmButton, Details, Field, Section, Select, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

/** An AI app on this computer, and whether the pass is in it (services.app_setup). */
interface AppStatus {
  app: string;
  label: string;
  found: boolean;
  added: boolean;
  current: boolean; // as this install runs it now
  problem: string | null; // why its settings file was left alone
}

interface PassInfo {
  name: string;
  project: string | null; // null: every client, the AI app choosing one at a time
  project_title: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  setup: { server: string; json: string; claude_code: string };
  apps: AppStatus[];
}

interface PassList {
  desktop: boolean;
  passes: PassInfo[];
}

const EVERY = "*every*"; // the choice for a pass for every client (no project can be named so)
const errorText = (err: unknown) => (err instanceof Error ? err.message : String(err));

/** Settings > Passes: an AI app on this computer using one project's connections, through
 * Ringkeeper's checks. A pass's token never shows: the setup starts Ringkeeper's bridge,
 * which reads it from the vault. */
export function Passes() {
  const list = useApi<PassList>("passes");
  if (!list.data) return null;
  if (!list.data.desktop) {
    return (
      <Section id="passes" title="Passes">
        <p className="text-sm text-muted">Passes work with the desktop app for now: the AI app has to run on the same computer as Ringkeeper.</p>
      </Section>
    );
  }
  const live = list.data.passes.filter((p) => !p.revoked_at).length;
  return (
    <Section id="passes" title="Passes" summary={live ? `${live} in use` : "none yet"}>
      <p className="mb-3 text-sm text-muted">
        A pass lets an AI app on this computer (Claude Desktop, Claude Code or Cursor) use one project&apos;s connections, or every client&apos;s, one at a time. Every call goes through Ringkeeper, the same as
        a task&apos;s: it reaches only the chosen project&apos;s connections, changes to production wait for your approval, and what it changed can be undone.
      </p>
      <NewPass onMade={list.reload} />
      <div className="mt-4 space-y-3">
        {list.data.passes.map((p) => (
          <PassCard key={p.name} pass={p} onChanged={list.reload} />
        ))}
      </div>
    </Section>
  );
}

function NewPass({ onMade }: { onMade: () => void }) {
  const projects = useApi<{ name: string; title: string }[]>("projects");
  const [project, setProject] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const choices: [string, string][] = [...(projects.data ?? []).map((p): [string, string] => [p.name, p.title || p.name]), [EVERY, "Every client, one at a time"]];
  const chosen = project || choices[0]?.[0] || "";
  const every = chosen === EVERY;
  const suggested = every ? "every-client" : `${chosen}-app`;

  async function make() {
    setBusy(true);
    setError(null);
    try {
      await api("passes", { method: "POST", json: { name: name.trim() || suggested, project: every ? null : chosen } });
      setName("");
      onMade();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (choices.length === 1) return <p className="text-sm text-muted">Add a project first: a pass is for a project, or for every client&apos;s.</p>;
  return (
    <Card className="space-y-3">
      <h3 className="text-sm font-semibold">New pass</h3>
      <Select label="Project" value={chosen} onChange={setProject} options={choices} />
      {every ? (
        <p className="text-sm text-muted">
          One entry in the AI app for every client. The AI lists them with ringkeeper_list_clients and chooses one with ringkeeper_use_client; only that client&apos;s connections answer until it chooses another, in every chat in the app. Each switch is in Activity.
        </p>
      ) : null}
      <Field label="Name" value={name} onChange={setName} placeholder={suggested} hint="Lowercase letters, digits and dashes. The AI app lists it by this name." />
      {error && <Alert>{error}</Alert>}
      <Button kind="primary" onClick={make} busy={busy}>
        Make pass
      </Button>
    </Card>
  );
}

function PassCard({ pass, onChanged }: { pass: PassInfo; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);

  async function act(path: string, method: "POST" | "DELETE") {
    setError(null);
    try {
      await api(path, { method });
      onChanged();
    } catch (err) {
      setError(errorText(err));
    }
  }

  const state = pass.revoked_at ? (
    <Badge tone="bad">Revoked</Badge>
  ) : pass.last_used_at ? (
    <Badge tone="good">Used {timeAgo(pass.last_used_at)}</Badge>
  ) : (
    <Badge>Not used yet</Badge>
  );
  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold">{pass.name}</h3>
          <p className="text-xs text-muted">For {pass.project_title}</p>
        </div>
        {state}
      </div>
      {pass.revoked_at ? <InApps pass={pass} onChanged={onChanged} revoked /> : <Setup pass={pass} onChanged={onChanged} />}
      <Changes path={`passes/${pass.name}/changes`} heading="Data it changed through this pass" />
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        {pass.revoked_at ? (
          <ConfirmButton question={`Remove ${pass.name}? It's taken out of Claude Code and Cursor too. Its history stays in Activity.`} confirm="Remove" onConfirm={() => act(`passes/${pass.name}`, "DELETE")}>
            Remove
          </ConfirmButton>
        ) : (
          <ConfirmButton question={`Revoke ${pass.name}? The app's next call is refused.`} confirm="Revoke" onConfirm={() => act(`passes/${pass.name}/revoke`, "POST")}>
            Revoke
          </ConfirmButton>
        )}
      </div>
    </Card>
  );
}

/** Set it up in each AI app on this computer: one click each. The steps by hand stay, closed,
 * for an app Ringkeeper can't change (its file has comments, say). */
function Setup({ pass, onChanged }: { pass: PassInfo; onChanged: () => void }) {
  return (
    <div className="space-y-2">
      <p className="text-sm">Add it to an AI app, then start a new chat or session there. Ringkeeper has to be running while you use it.</p>
      <ClaudeDesktopRow pass={pass} />
      <InApps pass={pass} onChanged={onChanged} />
      <Details label="Add it by hand instead">
        <ByHand pass={pass} />
      </Details>
    </div>
  );
}

/** Claude Code and Cursor: added to or taken out of their settings files. On a revoked pass,
 * only the apps it's still in, to take it out. */
function InApps({ pass, onChanged, revoked = false }: { pass: PassInfo; onChanged: () => void; revoked?: boolean }) {
  const shown = revoked ? pass.apps.filter((a) => a.added) : pass.apps;
  if (!shown.length) return null;
  return (
    <>
      {revoked ? <p className="text-sm text-muted">It&apos;s still in these apps. Take it out, so they stop starting it.</p> : null}
      {shown.map((a) => (
        <AppRow key={a.app} pass={pass} status={a} onChanged={onChanged} revoked={revoked} />
      ))}
    </>
  );
}

function AppRow({ pass, status, onChanged, revoked }: { pass: PassInfo; status: AppStatus; onChanged: () => void; revoked: boolean }) {
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function act(method: "POST" | "DELETE") {
    setBusy(true);
    setError(null);
    setSaid(null);
    try {
      const done = await api<{ said: string }>(`passes/${pass.name}/apps/${status.app}`, { method });
      setSaid(done.said);
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  const state = !status.found ? (
    <span className="text-xs text-muted">Not on this computer</span>
  ) : status.problem ? (
    <Badge tone="warn">Can&apos;t change its settings</Badge>
  ) : status.added ? (
    status.current ? <Badge tone="good">Added</Badge> : <Badge tone="warn">Added for an older version</Badge>
  ) : null;
  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{status.label}</span>
        {state}
        <span className="ml-auto" />
        {status.found && !status.problem && !revoked && (!status.added || !status.current) ? (
          <Button kind="primary" busy={busy} onClick={() => void act("POST")}>
            {status.added ? "Update" : `Add to ${status.label}`}
          </Button>
        ) : null}
        {status.found && status.added ? (
          <Button busy={busy} onClick={() => void act("DELETE")}>
            Take it out
          </Button>
        ) : null}
      </div>
      {status.problem ? <p className="text-xs text-muted">{status.problem} Or add it by hand, below.</p> : null}
      {said ? <Alert tone="good">{said}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Claude Desktop: its extension, saved into Downloads and opened, so Claude asks whether to
 * install it. Its own Settings › Extensions takes it out. */
function ClaudeDesktopRow({ pass }: { pass: PassInfo }) {
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function add() {
    setBusy(true);
    setError(null);
    try {
      const done = await api<{ said: string }>(`passes/${pass.name}/apps/claude-desktop`, { method: "POST" });
      setSaid(done.said);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-xl border border-line p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">Claude Desktop</span>
        <span className="ml-auto" />
        <Button kind="primary" busy={busy} onClick={() => void add()}>
          Add to Claude Desktop
        </Button>
      </div>
      {said ? <Alert tone="good">{said}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

const BY_HAND = {
  "claude-code": { label: "Claude Code" },
  cursor: { label: "Cursor" },
  "claude-desktop": { label: "Claude Desktop" },
} as const;

/** The steps by hand, for an app whose settings Ringkeeper can't change. */
function ByHand({ pass }: { pass: PassInfo }) {
  const [app, setApp] = useState<keyof typeof BY_HAND>("claude-code");
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="By hand in">
        {(Object.keys(BY_HAND) as (keyof typeof BY_HAND)[]).map((key) => (
          <Button key={key} kind={key === app ? "primary" : "secondary"} onClick={() => setApp(key)}>
            {BY_HAND[key].label}
          </Button>
        ))}
      </div>
      {app === "claude-desktop" ? (
        <>
          <p className="text-sm">
            <b>Quit Claude completely first</b> (from its icon by the clock, not just the window): it rewrites its settings file while it runs, and your change would be lost. Then add this to{" "}
            <span className="font-mono text-xs">claude_desktop_config.json</span> (in <span className="font-mono text-xs">%APPDATA%\Claude</span> on Windows,{" "}
            <span className="font-mono text-xs">~/Library/Application Support/Claude</span> on a Mac), beside any servers already there, and start Claude.
          </p>
          <CopyBlock key={app} text={pass.setup.json} />
        </>
      ) : app === "claude-code" ? (
        <>
          <p className="text-sm">Run this in a terminal. It adds the pass for every folder you use Claude Code in.</p>
          <CopyBlock key={app} text={pass.setup.claude_code} />
        </>
      ) : (
        <>
          <p className="text-sm">Put this in .cursor/mcp.json in your home folder (or in one project&apos;s folder, for that project only). If the file already lists servers, add this one beside them.</p>
          <CopyBlock key={app} text={pass.setup.json} />
        </>
      )}
    </div>
  );
}

function CopyBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-sunken p-3 text-xs">{text}</pre>
      <Button
        onClick={async () => {
          await navigator.clipboard.writeText(text);
          setCopied(true);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </>
  );
}
