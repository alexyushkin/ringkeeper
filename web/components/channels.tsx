"use client";

import { useState } from "react";

import { browserZone } from "@/components/routines";
import { Alert, Badge, Button, Card, ConfirmButton, Details, Field, Select, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { useScope } from "@/lib/client/scope";

/** Settings > Notifications: what each channel sends, and Slack and Discord channels
 * (ringkeeper.channels). Tokens go to the vault and never come back to this page. */

export interface Prefs {
  events: string[];
  quiet: { from: string; to: string; tz: string } | null;
  clients: string[];
  projects: string[];
}

export interface Channel {
  id: string;
  kind: "slack" | "discord";
  name: string;
  enabled: boolean;
  target: string;
  approver: string;
  about: Record<string, string>;
  prefs: Prefs;
  running: boolean;
  error: string | null;
}

interface Channels {
  telegram: { prefs: Prefs };
  channels: Channel[];
}

interface Target {
  id: string;
  name: string;
}

const EVENTS: [string, string, string][] = [
  ["approvals", "Approvals and questions", "Anything waiting for you."],
  ["routines", "Routine results", "A summary of each run, for routines set to send one."],
  ["failures", "Failures", "Tasks that failed, timed out or ran out of budget."],
  ["finished", "Finished tasks", "Every task that succeeded. Can be a lot."],
];

const KINDS = { slack: "Slack", discord: "Discord" };

function message(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

export function useChannels() {
  return useApi<Channels>("channels");
}

/** What a channel sends: events, quiet hours, and which clients and projects. */
export function ChannelPrefs({ prefs, onSave }: { prefs: Prefs; onSave: (prefs: Prefs) => Promise<unknown> }) {
  const { clients } = useScope();
  const projects = useApi<{ name: string; owner: string }[]>("projects");
  const [draft, setDraft] = useState<Prefs>(prefs);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const everything = draft.clients.length === 0 && draft.projects.length === 0;

  function flip(list: string[], value: string): string[] {
    return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
  }

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      await onSave(draft);
      setSaved(true);
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">What to send</legend>
        {EVENTS.map(([key, label, hint]) => (
          <label key={key} className="flex items-start gap-2 text-sm">
            <input type="checkbox" className="mt-1" checked={draft.events.includes(key)} onChange={() => setDraft({ ...draft, events: flip(draft.events, key) })} />
            <span>
              {label}
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </label>
        ))}
      </fieldset>
      <Toggle
        label="Quiet hours"
        checked={draft.quiet !== null}
        onChange={(on) => setDraft({ ...draft, quiet: on ? { from: "22:00", to: "07:00", tz: browserZone() } : null })}
        hint="Nothing is sent then; what came up is sent when they end (an approval only if it's still waiting)."
      />
      {draft.quiet ? (
        <div className="grid grid-cols-2 gap-3">
          <Field label="From" type="time" value={draft.quiet.from} onChange={(from) => setDraft({ ...draft, quiet: { ...draft.quiet!, from, tz: browserZone() } })} />
          <Field label="To" type="time" value={draft.quiet.to} onChange={(to) => setDraft({ ...draft, quiet: { ...draft.quiet!, to, tz: browserZone() } })} />
        </div>
      ) : null}
      <fieldset className="space-y-2">
        <legend className="text-sm font-medium">About which clients and projects</legend>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={everything} onChange={() => setDraft({ ...draft, clients: [], projects: [] })} disabled={everything} />
          All of them
        </label>
        {clients.map((client) => (
          <div key={client.slug} className="space-y-1">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={draft.clients.includes(client.slug)} onChange={() => setDraft({ ...draft, clients: flip(draft.clients, client.slug) })} />
              {client.name} <span className="text-xs text-muted">(all its projects)</span>
            </label>
            {draft.clients.includes(client.slug)
              ? null
              : (projects.data ?? [])
                  .filter((p) => p.owner === client.slug)
                  .map((p) => (
                    <label key={p.name} className="ml-6 flex items-center gap-2 text-sm">
                      <input type="checkbox" checked={draft.projects.includes(p.name)} onChange={() => setDraft({ ...draft, projects: flip(draft.projects, p.name) })} />
                      {p.name}
                    </label>
                  ))}
          </div>
        ))}
        <p className="text-xs text-muted">&quot;Which project?&quot; questions always come, since they aren&apos;t about one yet.</p>
      </fieldset>
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex items-center gap-3">
        <Button kind="primary" busy={busy} onClick={save}>
          Save
        </Button>
        {saved ? <span className="text-sm text-muted">Saved.</span> : null}
      </div>
    </div>
  );
}

export function TelegramPrefs({ prefs, onSaved }: { prefs: Prefs; onSaved: () => void }) {
  return (
    <Details label="What Telegram sends">
      <ChannelPrefs
        prefs={prefs}
        onSave={async (next) => {
          await api("channels/telegram/prefs", { method: "PUT", json: next });
          onSaved();
        }}
      />
    </Details>
  );
}

function targetLabel(channel: Channel): string {
  if (channel.target.startsWith("user:") || /^[UW]/.test(channel.target)) return "A DM with you";
  return channel.target;
}

/** One Slack or Discord channel: where it posts, who may answer, what it sends. */
export function ChannelCard({ channel, onChanged }: { channel: Channel; onChanged: () => void }) {
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const where = channel.kind === "slack" ? channel.about.team : channel.about.bot;

  async function put(values: Record<string, unknown>) {
    await api(`channels/${channel.id}`, { method: "PUT", json: values });
    onChanged();
  }

  async function test() {
    setTesting(true);
    setError(null);
    setNotice(null);
    try {
      await api(`channels/${channel.id}/test`, { method: "POST" });
      setNotice("Sent. Press its button: it should say that's you.");
    } catch (err) {
      setError(message(err));
    } finally {
      setTesting(false);
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-medium">{channel.name}</p>
          <p className="text-sm text-muted">
            {KINDS[channel.kind]}
            {where ? ` · ${where}` : ""} · {targetLabel(channel)}
          </p>
        </div>
        {!channel.enabled ? <Badge>Off</Badge> : channel.error ? <Badge tone="bad">Not running</Badge> : channel.running ? <Badge tone="good">On</Badge> : <Badge tone="warn">Starting</Badge>}
      </div>
      {channel.enabled && channel.error ? <Alert>{channel.error}</Alert> : null}
      <Toggle label="On" checked={channel.enabled} onChange={(on) => put({ enabled: on }).catch((err) => setError(message(err)))} hint="Turned back on, it sends only what happens from then." />
      <p className="text-sm text-muted">
        Only {channel.kind === "slack" ? "member" : "user"} ID {channel.approver} can answer from here; anyone else is told they&apos;re not allowed.
      </p>
      <div className="flex flex-wrap gap-2">
        <Button busy={testing} onClick={test}>
          Send test
        </Button>
        <ConfirmButton
          question={`Remove ${channel.name}? Its tokens are deleted from the vault; the ${KINDS[channel.kind]} app itself stays until you delete it there.`}
          confirm="Remove"
          onConfirm={async () => {
            await api(`channels/${channel.id}`, { method: "DELETE" });
            onChanged();
          }}
        >
          Remove
        </ConfirmButton>
      </div>
      {notice ? <Alert tone="good">{notice}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      <Details label="What it sends">
        <ChannelPrefs prefs={channel.prefs} onSave={(prefs) => put({ prefs })} />
      </Details>
    </Card>
  );
}

function Step({ n, title, children }: { n: number; title: string; children: React.ReactNode }) {
  return (
    <li className="space-y-2">
      <p className="font-medium">
        {n}. {title}
      </p>
      <div className="space-y-2 text-sm text-muted">{children}</div>
    </li>
  );
}

function Outside({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <a href={href} target="_blank" rel="noreferrer" className="text-accent underline">
      {children}
    </a>
  );
}

/** Where it posts and who may answer, once the tokens check out. */
function Destination({
  kind,
  targets,
  onReload,
  onSave,
}: {
  kind: "slack" | "discord";
  targets: Target[];
  onReload: () => void;
  onSave: (values: { name: string; target: string; approver: string }) => Promise<void>;
}) {
  const [where, setWhere] = useState(targets[0]?.id ?? "dm");
  const [approver, setApprover] = useState("");
  const [name, setName] = useState(KINDS[kind]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const idWord = kind === "slack" ? "member ID" : "user ID";
  const howToId =
    kind === "slack"
      ? "In Slack: your profile picture > Profile > ⋮ (More) > Copy member ID. It starts with U."
      : "In Discord: User Settings > Advanced > turn on Developer Mode; then right-click your name > Copy User ID.";

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const id = approver.trim();
      const target = where === "dm" ? (kind === "slack" ? id : `user:${id}`) : where;
      await onSave({ name, target, approver: id });
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <Select label="Where it posts" value={where} onChange={setWhere} options={[...targets.map((t): [string, string] => [t.id, t.name]), ["dm", "A DM with me"]]} />
      <Button kind="ghost" onClick={onReload}>
        Refresh channels
      </Button>
      <Field label={`Your ${idWord}`} value={approver} onChange={setApprover} hint={`Only presses from this ${idWord} count; anyone else in the channel is told they're not allowed. ${howToId}`} />
      <Field label="Name in Ringkeeper" value={name} onChange={setName} />
      {error ? <Alert>{error}</Alert> : null}
      <Button kind="primary" busy={busy} disabled={!approver.trim()} onClick={save}>
        Save and turn on
      </Button>
    </div>
  );
}

type Checked = { about: Record<string, string>; targets: Target[] };

function useCheck(tokens: Record<string, string>) {
  const [checked, setChecked] = useState<Checked | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function check() {
    setBusy(true);
    setError(null);
    try {
      setChecked(await api<Checked>("channels/check", { method: "POST", json: tokens }));
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  }
  return { checked, busy, error, check };
}

export function SlackSetup({ onDone }: { onDone: () => void }) {
  const manifest = useApi<Record<string, unknown>>("channels/slack/manifest");
  const [bot, setBot] = useState("");
  const [app, setApp] = useState("");
  const [copied, setCopied] = useState(false);
  const text = manifest.data ? JSON.stringify(manifest.data, null, 2) : "";
  const tokens = { kind: "slack", bot_token: bot.trim(), app_token: app.trim() };
  const { checked, busy, error, check } = useCheck(tokens);

  return (
    <ol className="space-y-4">
      <Step n={1} title="Create the Slack app">
        <p>
          Go to <Outside href="https://api.slack.com/apps?new_app=1">api.slack.com/apps</Outside>, choose <b>Create New App</b> &gt; <b>From a manifest</b>, pick your workspace, and paste this. It turns on Socket Mode, so nothing on this computer is opened to the internet.
        </p>
        <pre className="max-h-48 overflow-auto rounded-xl bg-sunken p-3 text-xs">{text}</pre>
        <Button
          onClick={async () => {
            await navigator.clipboard.writeText(text);
            setCopied(true);
          }}
        >
          {copied ? "Copied" : "Copy manifest"}
        </Button>
      </Step>
      <Step n={2} title="Install it, and copy the bot token">
        <p>
          In the app: <b>Install App</b> &gt; <b>Install to Workspace</b> &gt; Allow. Copy the <b>Bot User OAuth Token</b> (xoxb-...).
        </p>
        <Field label="Bot token" type="password" value={bot} onChange={setBot} autoComplete="off" />
      </Step>
      <Step n={3} title="Make an app-level token">
        <p>
          <b>Basic Information</b> &gt; <b>App-Level Tokens</b> &gt; <b>Generate Token and Scopes</b>: any name, add the scope <b>connections:write</b>, Generate. Copy it (xapp-...).
        </p>
        <Field label="App-level token" type="password" value={app} onChange={setApp} autoComplete="off" />
        <Button kind="primary" busy={busy} disabled={!bot.trim() || !app.trim()} onClick={check}>
          Check tokens
        </Button>
        {error ? <Alert>{error}</Alert> : null}
      </Step>
      {checked ? (
        <Step n={4} title={`Where it posts in ${checked.about.team || "your workspace"}`}>
          <p>Public channels work as they are; for a private one, add the app to it first (in the channel: /invite @Ringkeeper), then refresh.</p>
          <Destination
            kind="slack"
            targets={checked.targets}
            onReload={check}
            onSave={async (values) => {
              await api("channels", { method: "POST", json: { ...tokens, ...values } });
              onDone();
            }}
          />
        </Step>
      ) : null}
    </ol>
  );
}

export function DiscordSetup({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const tokens = { kind: "discord", bot_token: token.trim() };
  const { checked, busy, error, check } = useCheck(tokens);

  return (
    <ol className="space-y-4">
      <Step n={1} title="Create the application">
        <p>
          Go to <Outside href="https://discord.com/developers/applications">discord.com/developers/applications</Outside> &gt; <b>New Application</b>, and name it Ringkeeper.
        </p>
      </Step>
      <Step n={2} title="Copy the bot token">
        <p>
          <b>Bot</b> &gt; <b>Reset Token</b>, and copy it. No privileged intents are needed: leave them off.
        </p>
        <Field label="Bot token" type="password" value={token} onChange={setToken} autoComplete="off" />
        <Button kind="primary" busy={busy} disabled={!token.trim()} onClick={check}>
          Check token
        </Button>
        {error ? <Alert>{error}</Alert> : null}
      </Step>
      {checked ? (
        <>
          <Step n={3} title={`Invite ${checked.about.bot || "the bot"} to your server`}>
            <p>
              <Outside href={checked.about.invite ?? "https://discord.com/developers/applications"}>Open the invite link</Outside>, pick your server, and Authorize. It asks only to see channels and send messages. Then refresh the channels below.
            </p>
          </Step>
          <Step n={4} title="Where it posts">
            <p>For a DM, the bot and you must share a server.</p>
            <Destination
              kind="discord"
              targets={checked.targets}
              onReload={check}
              onSave={async (values) => {
                await api("channels", { method: "POST", json: { ...tokens, ...values } });
                onDone();
              }}
            />
          </Step>
        </>
      ) : null}
    </ol>
  );
}

/** The Slack or Discord section: its channels, and adding one. */
export function KindSection({ kind, list, onChanged }: { kind: "slack" | "discord"; list: Channel[]; onChanged: () => void }) {
  const [adding, setAdding] = useState(false);
  const mine = list.filter((c) => c.kind === kind);
  function done() {
    setAdding(false);
    onChanged();
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        {kind === "slack"
          ? "Your own Slack app posts approvals and results with Approve / Deny / Open buttons. It connects out from this computer (Socket Mode): no public address."
          : "Your own Discord bot posts approvals and results with Approve / Deny / Open buttons. It connects out from this computer: no public address."}{" "}
        Messages carry a short summary only, never secrets or SQL. Approve is offered only where it&apos;s safe without the details; production changes and deletes are opened in Ringkeeper.
      </p>
      {mine.map((channel) => (
        <ChannelCard key={channel.id} channel={channel} onChanged={onChanged} />
      ))}
      {adding ? (
        kind === "slack" ? <SlackSetup onDone={done} /> : <DiscordSetup onDone={done} />
      ) : (
        <Button kind={mine.length ? "secondary" : "primary"} onClick={() => setAdding(true)}>
          {mine.length ? `Add another ${KINDS[kind]} channel` : `Set up ${KINDS[kind]}`}
        </Button>
      )}
    </div>
  );
}
