"use client";

import { useState } from "react";

import { Changes } from "@/components/changes";
import { Alert, Badge, Button, Card, ConfirmButton, Field, Section, Select, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface PassInfo {
  name: string;
  project: string;
  project_title: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  setup: { server: string; json: string; claude_code: string };
}

interface PassList {
  desktop: boolean;
  passes: PassInfo[];
}

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
        A pass lets an AI app on this computer, like Claude Code or Cursor, use one project&apos;s connections. Every call goes through Ringkeeper, the same as a task&apos;s: it can&apos;t reach
        another project or another client, changes to production wait for your approval, and what it changed can be undone.
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
  const choices: [string, string][] = (projects.data ?? []).map((p) => [p.name, p.title || p.name]);
  const chosen = project || choices[0]?.[0] || "";

  async function make() {
    setBusy(true);
    setError(null);
    try {
      await api("passes", { method: "POST", json: { name: name.trim() || `${chosen}-app`, project: chosen } });
      setName("");
      onMade();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!choices.length) return <p className="text-sm text-muted">Add a project first: a pass is for one project.</p>;
  return (
    <Card className="space-y-3">
      <h3 className="text-sm font-semibold">New pass</h3>
      <Select label="Project" value={chosen} onChange={setProject} options={choices} />
      <Field label="Name" value={name} onChange={setName} placeholder={`${chosen}-app`} hint="Lowercase letters, digits and dashes. The AI app lists it by this name." />
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
      {!pass.revoked_at && <Setup pass={pass} />}
      <Changes path={`passes/${pass.name}/changes`} heading="Data it changed through this pass" />
      {error && <Alert>{error}</Alert>}
      <div className="flex flex-wrap gap-2">
        {pass.revoked_at ? (
          <ConfirmButton question={`Remove ${pass.name}? Its history stays in Activity.`} confirm="Remove" onConfirm={() => act(`passes/${pass.name}`, "DELETE")}>
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

const APPS = {
  "claude-code": { label: "Claude Code", where: "Run this in a terminal. It adds the pass for every folder you use Claude Code in." },
  cursor: { label: "Cursor", where: "Put this in .cursor/mcp.json in your home folder (or in one project's folder, for that project only). If the file already lists servers, add this one beside them." },
} as const;

function Setup({ pass }: { pass: PassInfo }) {
  const [app, setApp] = useState<keyof typeof APPS>("claude-code");
  const [copied, setCopied] = useState(false);
  const text = app === "claude-code" ? pass.setup.claude_code : pass.setup.json;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2" role="group" aria-label="Set it up in">
        {(Object.keys(APPS) as (keyof typeof APPS)[]).map((key) => (
          <Button
            key={key}
            kind={key === app ? "primary" : "secondary"}
            onClick={() => {
              setApp(key);
              setCopied(false);
            }}
          >
            {APPS[key].label}
          </Button>
        ))}
      </div>
      <p className="text-sm">{APPS[app].where} Ringkeeper has to be running while you use it.</p>
      <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-sunken p-3 text-xs">{text}</pre>
      <Button
        onClick={async () => {
          await navigator.clipboard.writeText(text);
          setCopied(true);
        }}
      >
        {copied ? "Copied" : "Copy"}
      </Button>
    </div>
  );
}
