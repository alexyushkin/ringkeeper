"use client";

import { useEffect, useId, useState } from "react";

import { Alert, Button, ConfirmButton, Empty, Field, Loading, Section, Select, TextArea, Toggle, timeAgo } from "@/components/ui";
import { LoadMore, ListTotal } from "@/components/tidy";
import { api, useApi, usePaged } from "@/lib/client/api";
import { projectName, useScope } from "@/lib/client/scope";

interface Where {
  connection: string;
  parent: string | null;
  data_source: string;
  url: string;
  agents_write?: boolean;
}

interface Status {
  backend: "local" | "notion";
  notion: Where | null;
  title: string;
  shared_notes: boolean;
  shared_available: boolean;
  notes: number;
  follows: boolean;
  cant_write: string | null; // Notion won't let the connection write there
  waiting: number; // notes kept here until it does
  connections: { name: string; title: string }[];
}

interface Note {
  id: string;
  note: string;
  agent: string | null;
  task: string | null;
  backend: string;
  waiting?: boolean; // Notion wouldn't take it yet: it goes there once it can
  created_at: string;
}

interface Found {
  id: string;
  kind: "page" | "data_source";
  title: string;
  url: string;
}

const BACKENDS: [string, string][] = [
  ["local", "Ringkeeper (local)"],
  ["notion", "Notion"],
];

/** A Notion connection, and the page or database it was given. `only`: pages (to make something
 * under) or databases (to use as they are); both when not given. */
function NotionPicker({ connections, connection, onConnection, place, onPlace, only }: { connections: { name: string; title: string }[]; connection: string; onConnection: (v: string) => void; place: string; onPlace: (v: string) => void; only?: "page" | "data_source" }) {
  const [words, setWords] = useState("");
  const pages = useApi<Found[]>(connection ? `notion/${connection}/pages` : null, { q: words.trim() || undefined });
  const found = (pages.data ?? []).filter((p) => !only || p.kind === only);
  const options: [string, string][] = found.map((p) => [`${p.kind}:${p.id}`, only ? p.title : p.kind === "page" ? `${p.title} (make a database under this page)` : `${p.title} (use this database)`]);
  const label = only === "data_source" ? "Database" : only === "page" ? "Page" : "Parent page";
  return (
    <div className="space-y-2">
      {connections.length ? (
        <Select label="Notion connection" value={connection} onChange={onConnection} options={[["", "Choose one"], ...connections.map((c): [string, string] => [c.name, c.title])]} />
      ) : (
        <Alert tone="warn">Add a Notion memory connection first, under Connections. Use one of this client&apos;s, or a shared one.</Alert>
      )}
      {connection ? (
        <>
          <Field label={only === "data_source" ? "Find a database" : "Find a page"} value={words} onChange={setWords} placeholder="Words from its title" />
          {pages.error ? <Alert>{pages.error}</Alert> : null}
          {pages.data && !found.length ? <Alert tone="warn">{only === "data_source" ? "Notion shows no database yet. In Notion, open the database, choose ••• › Connections, and add this connection." : "Notion shows nothing yet. In Notion, open the page, choose ••• › Connections, and add this connection."}</Alert> : null}
          {found.length ? <Select label={label} value={place} onChange={onPlace} options={[["", "Choose one"], ...options]} /> : null}
        </>
      ) : null}
    </div>
  );
}

function picked(place: string): { parent?: string; data_source?: string } {
  const [kind, id] = place.split(":");
  if (!id) return {};
  return kind === "page" ? { parent: id } : { data_source: id };
}

function NoteList({ project, status, onChange }: { project: string; status: Status; onChange: () => void }) {
  const [words, setWords] = useState("");
  const notes = usePaged<Note>(`projects/${project}/memory/notes`, { q: words.trim() || undefined });
  const [error, setError] = useState<string | null>(null);
  const [synced, setSynced] = useState<string | null>(null);

  async function sync() {
    setError(null);
    try {
      const done = await api<{ added: number; changed: number; forgotten: number }>(`projects/${project}/memory/sync`, { method: "POST" });
      setSynced(`From Notion: ${done.added} new, ${done.changed} changed, ${done.forgotten} forgotten.`);
      notes.reload();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function forget(id: string) {
    setError(null);
    try {
      await api(`projects/${project}/memory/notes/${id}`, { method: "DELETE" });
      notes.reload();
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-2">
      <AddNote
        project={project}
        onAdded={() => {
          notes.reload();
          onChange();
        }}
      />
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1">
          <Field label="Search notes" value={words} onChange={setWords} placeholder="Words from a note" />
        </div>
        {status.notion ? <Button onClick={() => void sync()}>Sync with Notion</Button> : null}
      </div>
      {synced ? <Alert tone="good">{synced}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
      {notes.error ? <Alert>{notes.error}</Alert> : null}
      <ListTotal total={notes.total} what={["note", "notes"]} searching={Boolean(words.trim())} archived={false} />
      {notes.loading && !notes.items ? <Loading /> : null}
      {notes.items?.length === 0 ? <Empty>{words ? "No notes match." : "No notes yet. Agents leave them as they work."}</Empty> : null}
      <ul className="space-y-2">
        {notes.items?.map((note) => (
          <li key={note.id} className="rounded-xl border border-line p-3 text-sm">
            <p className="whitespace-pre-wrap break-words">{note.note}</p>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted">
              <span>{note.agent ?? "A person"}</span>
              <span>{timeAgo(note.created_at)}</span>
              <span>{note.waiting ? "waiting to go to Notion" : note.backend === "notion" ? "in Notion" : "in Ringkeeper"}</span>
              <span className="ml-auto" />
              <ConfirmButton question={note.backend === "notion" && !note.waiting ? "Delete this note? It goes to Notion's trash." : "Delete this note? This can't be undone."} confirm="Delete note" onConfirm={() => forget(note.id)}>
                Delete
              </ConfirmButton>
            </div>
          </li>
        ))}
      </ul>
      <LoadMore list={notes} />
    </div>
  );
}

/** Where a project's agents keep notes between runs, and the notes. */
export function ProjectMemory({ project }: { project: string }) {
  const status = useApi<Status>(`projects/${project}/memory`);
  const [backend, setBackend] = useState("local");
  const [connection, setConnection] = useState("");
  const [place, setPlace] = useState("");
  const [how, setHow] = useState<"page" | "data_source">("page");
  const [shared, setShared] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = status.data;
  useEffect(() => {
    if (!data) return;
    setBackend(data.backend);
    setConnection(data.notion?.connection ?? "");
    setShared(data.shared_notes);
  }, [data]);
  if (status.error) return <Alert>{status.error}</Alert>;
  if (!data) return <Loading />;
  const attached = Boolean(data.notion);
  const ready = backend === "local" || attached || Boolean(connection && place);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      await api(`projects/${project}/memory`, {
        method: "PUT",
        json: { backend, shared_notes: shared, ...(place ? { connection, ...picked(place) } : {}) },
      });
      setPlace("");
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section id="memory" title="Memory" summary={`${data.notes} note${data.notes === 1 ? "" : "s"}, ${data.backend === "notion" ? "in Notion" : "in Ringkeeper"}`} defaultOpen={false}>
      <div className="space-y-3">
        <Select label="Keep agents' notes in" value={backend} onChange={setBackend} options={BACKENDS} hint="Agents read and write notes only with their memory tools, for this project alone." />
        {backend === "notion" ? (
          <>
            {data.notion ? (
              <p className="text-sm">
                Notes go to{" "}
                <a href={data.notion.url} target="_blank" rel="noreferrer" className="text-accent">
                  {data.title}
                </a>
                .
              </p>
            ) : data.follows ? (
              <p className="text-sm text-muted">Notion is on for every project: this one gets its database under its client&apos;s page when its first task starts.</p>
            ) : null}
            {data.notion && data.cant_write ? (
              <Alert tone="warn">
                Notion won&apos;t let this connection write there: {data.cant_write} {data.waiting === 1 ? "1 note waits here in Ringkeeper, and goes" : `${data.waiting} notes wait here in Ringkeeper, and go`} to Notion once it can.
              </Alert>
            ) : null}
            <PlaceChoice
              value={how}
              onChange={(next) => {
                setHow(next);
                setPlace("");
              }}
            />
            {how === "page" ? (
              <p className="text-sm text-muted">Ringkeeper makes a database called “{data.title}” under the page you choose, with one row per note: the agent, the date, the task it came from and who saved it.</p>
            ) : (
              <p className="text-sm text-muted">Notes go into a database that&apos;s already there. Someone else&apos;s Ringkeeper may write to it too. Columns it doesn&apos;t have are added when Notion allows. Another client&apos;s database, or anyone&apos;s My memory, can&apos;t be used.</p>
            )}
            <NotionPicker connections={data.connections} connection={connection} onConnection={setConnection} place={place} onPlace={setPlace} only={how} />
            <p className="text-xs text-muted">Notes you or other tools add there are picked up on the next run. Edits in Notion win, and deleted rows are forgotten.</p>
          </>
        ) : null}
        {data.shared_available ? <Toggle label="Use My memory and shared notes" checked={shared} onChange={setShared} hint="Agents here can read them, and add to My memory." /> : null}
        {error ? <Alert>{error}</Alert> : null}
        <Button kind="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
          Save
        </Button>
        <NoteList project={project} status={data} onChange={status.reload} />
      </div>
    </Section>
  );
}

/** Where a project's notes go in Notion: a database made now, or one that's already there. */
function PlaceChoice({ value, onChange }: { value: "page" | "data_source"; onChange: (v: "page" | "data_source") => void }) {
  const id = useId();
  const choices: ["page" | "data_source", string, string][] = [
    ["page", "Create a new one", "A database made for this project, under a page you choose."],
    ["data_source", "Use a database that's already in Notion", "One your team set up, or another Ringkeeper made."],
  ];
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-medium">Notion database</legend>
      <div className="grid gap-2 sm:grid-cols-2">
        {choices.map(([key, label, hint]) => (
          <label key={key} className={`flex cursor-pointer gap-2 rounded-xl border p-3 text-sm ${value === key ? "border-accent bg-accent-soft" : "border-line bg-surface hover:border-accent"}`}>
            <input type="radio" name={id} value={key} checked={value === key} onChange={() => onChange(key)} className="mt-0.5 size-4 accent-[var(--color-accent)]" />
            <span>
              <span className="block font-medium">{label}</span>
              <span className="block text-xs text-muted">{hint}</span>
            </span>
          </label>
        ))}
      </div>
    </fieldset>
  );
}

interface Personal {
  name: string;
  title: string;
  enabled: boolean;
  offered: boolean;
  listed: boolean;
  hint: string | null;
}

/** "My memory": the owner's preferences across clients, in a database of their own. */
export function MyMemory() {
  const status = useApi<{ mine: Where | null; connections: { name: string; title: string }[] }>("memory/mine");
  const [connection, setConnection] = useState("");
  const [place, setPlace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const data = status.data;
  useEffect(() => {
    if (data?.mine) setConnection(data.mine.connection);
  }, [data]);
  if (!data) return null;

  async function save() {
    setError(null);
    try {
      await api("memory/mine", { method: "PUT", json: { connection, ...picked(place) } });
      setPlace("");
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    await api("memory/mine", { method: "DELETE" });
    setConnection("");
    status.reload();
  }

  return (
    <Section id="my-memory" title="My memory" summary={data.mine ? "in Notion" : "off"} defaultOpen={false}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Your preferences, style and how you work, in a Notion database of your own. It works on any Notion plan. Agents search it when they start and add lasting preferences when they finish. Nothing about a client or project goes in, and emails, phone numbers and street addresses are taken out.</p>
        {!data.connections.length ? <Alert tone="warn">Add Notion memory as a shared tool under Connections first. A client&apos;s connection can&apos;t hold this.</Alert> : null}
        {data.mine ? (
          <a href={data.mine.url} target="_blank" rel="noreferrer" className="text-sm text-accent">
            Open in Notion
          </a>
        ) : null}
        {data.connections.length ? <NotionPicker connections={data.connections} connection={connection} onConnection={setConnection} place={place} onPlace={setPlace} only="page" /> : null}
        {data.connections.length ? <p className="text-xs text-muted">Ringkeeper makes it under the page you choose. A My memory database that&apos;s already in Notion may be someone else&apos;s, so it&apos;s never offered.</p> : null}
        {error ? <Alert>{error}</Alert> : null}
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" disabled={!connection || !place} onClick={() => void save()}>
            Save
          </Button>
          {data.mine ? (
            <ConfirmButton question="Stop using My memory? What's in Notion stays there." confirm="Stop using it" onConfirm={remove}>
              Stop using it
            </ConfirmButton>
          ) : null}
        </div>
      </div>
    </Section>
  );
}

/** Notion AI's own memory: the owner's preferences, on their shared Notion connection. */
export function PersonalMemory() {
  const status = useApi<{ connections: Personal[] }>("memory/personal");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  if (!status.data) return null;
  const all = status.data.connections;

  async function set(connection: string, enabled: boolean) {
    setError(null);
    try {
      await api("memory/personal", { method: "PUT", json: { connection, enabled } });
      if (enabled) await api(`connections/${connection}/tools`);
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function refresh(connection: string) {
    setBusy(connection);
    setError(null);
    try {
      await api(`connections/${connection}/tools`);
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Section id="notion-memory" title="Notion AI memory" summary={all.some((c) => c.enabled) ? "on" : "needs Notion Business plan"} defaultOpen={false}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Needs Notion Business plan or higher, with Notion AI. On other plans, use My memory above. It holds your own preferences in Notion&apos;s personal memory. Nothing about a client or project goes in, and that knowledge stays in each project&apos;s Memory.</p>
        {!all.length ? <Alert tone="warn">Add Notion as a shared tool under Connections, and sign in with your own account. A client&apos;s Notion connection can&apos;t use this.</Alert> : null}
        {all.map((connection) => (
          <div key={connection.name} className="space-y-2">
            <Toggle label={`Use Notion AI memory on ${connection.title}`} checked={connection.enabled} onChange={(on) => void set(connection.name, on)} hint="Searching and saving run without asking. Deleting a memory needs the connection's name typed." />
            {connection.hint ? <Alert tone="warn">{connection.hint}</Alert> : null}
            {connection.enabled ? (
              <Button busy={busy === connection.name} onClick={() => void refresh(connection.name)}>
                Refresh its tools
              </Button>
            ) : null}
          </div>
        ))}
        {error ? <Alert>{error}</Alert> : null}
      </div>
    </Section>
  );
}

/** The owner's own notes across clients, on a shared Notion connection. */
export function SharedNotes() {
  const status = useApi<{ shared: Where | null; connections: { name: string; title: string }[] }>("memory/shared");
  const [connection, setConnection] = useState("");
  const [place, setPlace] = useState("");
  const [write, setWrite] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const data = status.data;
  useEffect(() => {
    if (!data?.shared) return;
    setConnection(data.shared.connection);
    setWrite(Boolean(data.shared.agents_write));
  }, [data]);
  if (!data) return null;

  async function save() {
    setError(null);
    try {
      const where = place ? picked(place) : { parent: data?.shared?.parent ?? undefined, data_source: data?.shared?.data_source };
      await api("memory/shared", { method: "PUT", json: { connection, agents_write: write, ...where } });
      setPlace("");
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    await api("memory/shared", { method: "DELETE" });
    setConnection("");
    status.reload();
  }

  return (
    <Section id="shared-notes" title="Shared notes" summary={data.shared ? "in Notion" : "off"} defaultOpen={false}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Your own knowledge across clients, in Notion. Every project can read it unless you turn it off there. It needs a shared Notion connection, so keep client data out of it.</p>
        {data.shared ? (
          <a href={data.shared.url} target="_blank" rel="noreferrer" className="text-sm text-accent">
            Open in Notion
          </a>
        ) : null}
        <NotionPicker connections={data.connections} connection={connection} onConnection={setConnection} place={place} onPlace={setPlace} />
        <Toggle label="Agents may add to it" checked={write} onChange={setWrite} hint="Off: read-only to agents." />
        {error ? <Alert>{error}</Alert> : null}
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" disabled={!connection || !(place || data.shared)} onClick={() => void save()}>
            Save
          </Button>
          {data.shared ? (
            <ConfirmButton question="Stop using shared notes? What's in Notion stays there." confirm="Stop using them" onConfirm={remove}>
              Stop using them
            </ConfirmButton>
          ) : null}
        </div>
      </div>
    </Section>
  );
}

/** Where a project's notes go, for its About: here, or Notion with a link. */
export function NotesWhere({ project }: { project: string }) {
  const status = useApi<Status>(`projects/${project}/memory`);
  const data = status.data;
  if (!data) return <span className="text-muted">…</span>;
  if (data.backend === "notion" && data.notion) {
    return (
      <span>
        In Notion:{" "}
        <a href={data.notion.url} target="_blank" rel="noreferrer" className="text-accent">
          {data.title}
        </a>
      </span>
    );
  }
  return <span>{data.follows ? "Here in Ringkeeper until its first task, then in Notion" : "Here in Ringkeeper"}</span>;
}

interface Ask {
  key: string;
  what: "page" | "database";
  label: string;
  wanted: string;
  options: { id: string; title: string; url: string }[];
}

interface Done {
  set_up: string[];
  failed: Record<string, string>;
  joined: string[];
  created: string[];
  ask: Ask[];
}

/** What setting up joined, made and still asks about, in a sentence or two. */
function doneWords(done: Done): string {
  const failed = Object.keys(done.failed);
  const parts = [`${done.set_up.length} project${done.set_up.length === 1 ? "" : "s"} set up now.`];
  if (done.joined.length) parts.push(`Joined what was already in Notion: ${done.joined.join(", ")}.`);
  if (done.created.length) parts.push(`Made: ${done.created.join(", ")}.`);
  if (done.ask.length) parts.push(`${done.ask.length === 1 ? "One needs" : `${done.ask.length} need`} you to choose, below.`);
  if (failed.length) parts.push(`Notion refused ${failed.join(", ")}: they're tried again when a task starts.`);
  return parts.join(" ");
}

interface Every {
  asks: Ask[];
  every: { connection: string; root: string; title: string; url: string } | null;
  connections: { name: string; title: string }[];
  projects: { name: string; title: string; where: "local" | "notion"; url: string | null }[];
}

/** Notion for every project: one root page, a page per client, a database per project. */
export function EveryProject({ connection: fixed }: { connection?: string }) {
  const status = useApi<Every>("memory/every");
  const [connection, setConnection] = useState(fixed ?? "");
  const [place, setPlace] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = status.data;
  useEffect(() => {
    if (!fixed && data?.every) setConnection(data.every.connection);
  }, [data, fixed]);
  if (!data) return null;
  const usable = fixed ? data.connections.filter((c) => c.name === fixed) : data.connections;
  const root = picked(place).parent;

  async function save() {
    setBusy(true);
    setError(null);
    setSaid(null);
    try {
      const done = await api<Done>("memory/every", { method: "PUT", json: { connection, root } });
      setSaid(`On. ${doneWords(done)}`);
      setPlace("");
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function pick(key: string, choice: string) {
    setError(null);
    setSaid(null);
    try {
      const done = await api<Done>("memory/every/choose", { method: "PUT", json: { key, choice } });
      setSaid(doneWords(done));
      status.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function stop() {
    await api("memory/every", { method: "DELETE" });
    setSaid("New projects keep their notes here now. The others stay where they are.");
    status.reload();
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">One Notion page for every client&apos;s notes. Ringkeeper makes a page per client under it and a database per project under its client&apos;s page, so one client&apos;s page never holds another&apos;s notes. Pages and databases already there with those titles, from your team&apos;s Ringkeeper, are joined, not made again. Projects already in Notion keep their place. New projects follow.</p>
      {data.every ? (
        <p className="text-sm">
          On, under{" "}
          <a href={data.every.url} target="_blank" rel="noreferrer" className="text-accent">
            {data.every.title}
          </a>
          .
        </p>
      ) : null}
      {usable.length ? (
        <NotionPicker connections={usable} connection={connection} onConnection={setConnection} place={place} onPlace={setPlace} only="page" />
      ) : (
        <Alert tone="warn">This needs a Notion memory connection that&apos;s your own tool, since it holds every client&apos;s notes. A client&apos;s connection holds only that client&apos;s notes: choose it in each of their projects&apos; Memory.</Alert>
      )}
      {data.asks.map((ask) => (
        <div key={ask.key} className="space-y-2 rounded-xl border border-warn p-3 text-sm">
          <p>
            Which Notion {ask.what} is {ask.what === "page" ? `${ask.label}'s` : `for ${ask.label}`}? {ask.options.length > 1 ? `${ask.options.length} are there` : "One is there"} with a title like “{ask.wanted}”. Ringkeeper doesn&apos;t guess: choose one, or make a new one.
          </p>
          <div className="flex flex-wrap gap-2">
            {ask.options.map((option) => (
              <span key={option.id} className="flex items-center gap-1">
                <Button onClick={() => void pick(ask.key, option.id)}>Use “{option.title}”</Button>
                <a href={option.url} target="_blank" rel="noreferrer" className="text-xs text-accent">
                  open
                </a>
              </span>
            ))}
            <Button kind="ghost" onClick={() => void pick(ask.key, "new")}>
              Make a new one
            </Button>
          </div>
        </div>
      ))}
      {error ? <Alert>{error}</Alert> : null}
      {said ? <Alert tone="good">{said}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!connection || !root} onClick={() => void save()}>
          Use Notion for every project
        </Button>
        {data.every ? (
          <ConfirmButton question="Stop putting new projects' notes in Notion? Every project keeps where its notes are now." confirm="Stop" onConfirm={stop}>
            Stop for new projects
          </ConfirmButton>
        ) : null}
      </div>
      {data.projects.length ? (
        <ul className="space-y-1 text-sm">
          {data.projects.map((p) => (
            <li key={p.name} className="flex flex-wrap gap-2">
              <span>{p.title}</span>
              {p.where === "notion" && p.url ? (
                <a href={p.url} target="_blank" rel="noreferrer" className="text-accent">
                  in Notion
                </a>
              ) : (
                <span className="text-muted">here in Ringkeeper</span>
              )}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

/** What a task saved to memory, under its result. Nothing when it saved nothing. */
export function SavedToMemory({ task }: { task: string }) {
  const saved = useApi<{ note: string; where: string }[]>(`tasks/${task}/memory`);
  if (!saved.data?.length) return null;
  return (
    <div className="space-y-1 rounded-xl border border-line p-2 text-sm">
      <p className="text-xs font-medium text-muted">Saved to memory</p>
      <ul className="space-y-1">
        {saved.data.map((n, i) => (
          <li key={i} className="whitespace-pre-wrap break-words">
            {n.note} <span className="text-xs text-muted">· {n.where}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const MOST_CHARS = 2000;

/** "Remember this" on a message or a result: an editable note for one project's memory. */
export function RememberThis({ text, projects, task }: { text: string; projects: string[]; task?: string }) {
  const scope = useScope();
  const offered = projects.length ? projects : scope.projects.map((p) => p.name);
  const [open, setOpen] = useState(false);
  const [note, setNote] = useState("");
  const [project, setProject] = useState(offered[0] ?? "");
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!offered.length) return null;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const kept = await api<{ where: string }>(`projects/${project}/memory/notes`, { method: "POST", json: { note: note.trim(), ...(task ? { task } : {}) } });
      setSaid(`Saved to ${projectName(project)}'s memory, ${kept.where === "Notion" ? "in Notion" : "here in Ringkeeper"}.`);
      setOpen(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        <Button
          kind="ghost"
          onClick={() => {
            setNote(text.trim().slice(0, MOST_CHARS));
            setSaid(null);
            setOpen(true);
          }}
        >
          Remember this
        </Button>
        {said ? <span className="text-xs text-good">{said}</span> : null}
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded-xl border border-line p-2">
      <TextArea label="Note to keep" value={note} onChange={setNote} rows={4} hint={`Keep the point: at most ${MOST_CHARS} characters. Secrets, card and bank numbers aren't kept.`} />
      {offered.length > 1 ? <Select label="Project" value={project} onChange={setProject} options={offered.map((p): [string, string] => [p, projectName(p)])} /> : null}
      {error ? <Alert>{error}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        <Button kind="primary" busy={busy} disabled={!note.trim() || note.length > MOST_CHARS || !project} onClick={() => void save()}>
          Save to memory
        </Button>
        <Button kind="ghost" onClick={() => setOpen(false)}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** A note typed by hand on the project's Memory: the same filter and place as an agent's. */
function AddNote({ project, onAdded }: { project: string; onAdded: () => void }) {
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [said, setSaid] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    setError(null);
    setSaid(null);
    try {
      const kept = await api<{ where: string }>(`projects/${project}/memory/notes`, { method: "POST", json: { note: note.trim() } });
      setNote("");
      setSaid(kept.where === "Notion" ? "Saved, in Notion." : "Saved.");
      onAdded();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2">
      <TextArea label="Add a note" value={note} onChange={setNote} rows={2} placeholder="Something later runs should know, like where a client's invoices live" />
      {error ? <Alert>{error}</Alert> : null}
      {said ? <Alert tone="good">{said}</Alert> : null}
      <Button disabled={!note.trim() || note.length > MOST_CHARS} busy={busy} onClick={() => void save()}>
        Add note
      </Button>
    </div>
  );
}
