"use client";

import { useEffect, useState } from "react";

import { Alert, Button, ConfirmButton, Empty, Field, Loading, Section, Select, Toggle, timeAgo } from "@/components/ui";
import { LoadMore, ListTotal } from "@/components/tidy";
import { api, useApi, usePaged } from "@/lib/client/api";

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
  connections: { name: string; title: string }[];
}

interface Note {
  id: string;
  note: string;
  agent: string | null;
  task: string | null;
  backend: string;
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

/** A Notion connection, and the page or database it was given. */
function NotionPicker({ connections, connection, onConnection, place, onPlace }: { connections: { name: string; title: string }[]; connection: string; onConnection: (v: string) => void; place: string; onPlace: (v: string) => void }) {
  const [words, setWords] = useState("");
  const pages = useApi<Found[]>(connection ? `notion/${connection}/pages` : null, { q: words.trim() || undefined });
  const options: [string, string][] = (pages.data ?? []).map((p) => [`${p.kind}:${p.id}`, p.kind === "page" ? `${p.title} (make a database under this page)` : `${p.title} (use this database)`]);
  return (
    <div className="space-y-2">
      {connections.length ? (
        <Select label="Notion connection" value={connection} onChange={onConnection} options={[["", "Choose one"], ...connections.map((c): [string, string] => [c.name, c.title])]} />
      ) : (
        <Alert tone="warn">Add a Notion memory connection first, under Connections. Use one of this client&apos;s, or a shared one.</Alert>
      )}
      {connection ? (
        <>
          <Field label="Find a page" value={words} onChange={setWords} placeholder="Words from its title" />
          {pages.error ? <Alert>{pages.error}</Alert> : null}
          {pages.data && !pages.data.length ? <Alert tone="warn">Notion shows nothing yet. In Notion, open the page, choose ••• › Connections, and add this connection.</Alert> : null}
          {pages.data?.length ? <Select label="Parent page" value={place} onChange={onPlace} options={[["", "Choose one"], ...options]} /> : null}
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
              <span>{note.backend === "notion" ? "in Notion" : "in Ringkeeper"}</span>
              <span className="ml-auto" />
              <ConfirmButton question={note.backend === "notion" ? "Delete this note? It goes to Notion's trash." : "Delete this note? This can't be undone."} confirm="Delete note" onConfirm={() => forget(note.id)}>
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
            ) : (
              <p className="text-sm text-muted">Ringkeeper makes a database called “{data.title}”, with one row per note: the agent, the date and the task it came from.</p>
            )}
            <NotionPicker connections={data.connections} connection={connection} onConnection={setConnection} place={place} onPlace={setPlace} />
            <p className="text-xs text-muted">Notes you or other tools add there are picked up on the next run. Edits in Notion win, and deleted rows are forgotten.</p>
          </>
        ) : null}
        {data.shared_available ? <Toggle label="Use my shared notes" checked={shared} onChange={setShared} hint="Agents here can read them." /> : null}
        {error ? <Alert>{error}</Alert> : null}
        <Button kind="primary" busy={busy} disabled={!ready} onClick={() => void save()}>
          Save
        </Button>
        <NoteList project={project} status={data} onChange={status.reload} />
      </div>
    </Section>
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

/** "Use my Notion memory": the owner's own preferences, on their shared Notion connection. */
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
    <Section id="notion-memory" title="Notion memory" summary={all.some((c) => c.enabled) ? "on" : "off"} defaultOpen={false}>
      <div className="space-y-3">
        <p className="text-sm text-muted">Your own preferences, style and how you work, in Notion&apos;s personal memory. Agents search it when they start and save lasting preferences when they finish. Nothing about a client or project goes in: those saves are refused, and that knowledge stays in each project&apos;s Memory.</p>
        {!all.length ? <Alert tone="warn">Add Notion as a shared tool under Connections, and sign in with your own account. A client&apos;s Notion connection can&apos;t use this.</Alert> : null}
        {all.map((connection) => (
          <div key={connection.name} className="space-y-2">
            <Toggle label={`Use my Notion memory on ${connection.title}`} checked={connection.enabled} onChange={(on) => void set(connection.name, on)} hint="Searching and saving run without asking. Deleting a memory needs the connection's name typed." />
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
