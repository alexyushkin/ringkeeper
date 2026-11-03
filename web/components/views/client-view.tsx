"use client";

import Link from "@/components/link";
import { useState } from "react";

import { LibraryCard, NoProjects, useLibrary } from "@/components/connection-library";
import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { ProjectForm } from "@/components/forms";
import { ScopedTasks, useTaskSummary } from "@/components/scoped-tasks";
import { Alert, Button, ConfirmButton, Empty, Field, Loading, Section, Select, Tabs, type Tab } from "@/components/ui";
import { api } from "@/lib/client/api";
import { projectHref } from "@/lib/client/routes";
import { clientName, useScope } from "@/lib/client/scope";
import { useRouter } from "next/navigation";

/** One client: their projects and their connection library. A connection is added
 * once and linked to any of their projects, each link narrowed if it needs to be. */
export function ClientView({ name }: { name: string }) {
  const library = useLibrary(name);
  const display = clientName(name);
  const tasks = useTaskSummary({ client: name });
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);

  function done(connection: string) {
    setAdded(connection);
    setAdding(false);
    library.reload();
  }

  const production = library.connections.filter((c) => c.env === "prod").length;
  const summary = `${library.connections.length} connection${library.connections.length === 1 ? "" : "s"}${production ? ` · ${production} production` : ""}`;

  const tabs: Tab[] = [
    {
      key: "overview",
      label: "Overview",
      content: () => (
        <Section id="projects" title="Projects" summary={String(library.projects.length)}>
          {library.projects.length ? (
            <ul className="space-y-1">
              {library.projects.map((p) => (
                <li key={p}>
                  <Link href={projectHref(p)} className="text-accent">
                    {p}
                  </Link>
                  <span className="ml-2 text-xs text-muted">{library.connections.filter((c) => c.projects.includes(p)).length} of {display}&apos;s connections</span>
                </li>
              ))}
            </ul>
          ) : (
            <NoProjects client={name} />
          )}
        </Section>
      ),
    },
    {
      key: "connections",
      label: "Connections",
      count: library.connections.length,
      content: () => (
        <Section id="connections" title="Connections" summary={summary}>
          <p className="text-sm text-muted">
            {display}&apos;s services. Connect each one once, then link it to any of {display}&apos;s projects; a link can be narrowed to one database, base or project. Other clients&apos; projects can&apos;t use these.
          </p>
          {library.error ? <Alert>{library.error}</Alert> : null}
          {library.loading && !library.connections.length ? <Loading /> : null}
          {!library.loading && !library.projects.length ? <NoProjects client={name} /> : null}
          <div className="space-y-2">
            {library.connections.map((connection) => (
              <LibraryCard key={connection.name} connection={connection} projects={library.projects} services={library.services} onChange={library.reload} />
            ))}
          </div>
          {!library.loading && !library.connections.length && !adding ? <Empty>No connections for {display} yet. Connect their first service below.</Empty> : null}
          {added ? (
            <ConnectionAdded
              name={added}
              onLinked={library.reload}
              onAnother={() => {
                setAdded(null);
                setAdding(true);
              }}
              onDone={() => {
                setAdded(null);
                library.reload();
              }}
            />
          ) : adding ? (
            <div className="space-y-4 rounded-xl border border-line p-3">
              <h3 className="font-medium">Connect a service for {display}</h3>
              <Catalog client={name} kind="client" onAdded={done} />
              <Button kind="ghost" onClick={() => setAdding(false)}>
                Cancel
              </Button>
            </div>
          ) : (
            <Button kind={library.connections.length ? "secondary" : "primary"} onClick={() => setAdding(true)}>
              Connect a service
            </Button>
          )}
        </Section>
      ),
    },
    { key: "tasks", label: "Tasks", count: tasks.count, content: () => <ScopedTasks client={name} /> },
    {
      key: "settings",
      label: "Settings",
      content: () => (
        <>
          <Section id="new-project" title={`Add a project for ${display}`} defaultOpen={!library.projects.length}>
            <ProjectForm client={name} onSaved={() => library.reload()} />
          </Section>
          <Section id="client-name" title="Name" summary={display} defaultOpen={false}>
            <ClientSettings slug={name} name={display} hasWork={Boolean(library.projects.length || library.connections.length)} />
          </Section>
        </>
      ),
    },
  ];

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-1 text-xl font-semibold">{display}</h1>
      <p className="mb-4 text-sm text-muted">
        {library.projects.length} project{library.projects.length === 1 ? "" : "s"} · {library.connections.length} connection{library.connections.length === 1 ? "" : "s"}
        {production ? ` (${production} production)` : ""}
        {tasks.running ? ` · ${tasks.running} running` : ""}
      </p>
      <Tabs id={`client-${name}`} tabs={tabs} />
    </>
  );
}

/** A client's name as shown (its slug, in URLs and IDs, stays), and removing it: at
 * once when it has no projects or connections, else after moving them to another. */
function ClientSettings({ slug, name, hasWork }: { slug: string; name: string; hasWork: boolean }) {
  const scope = useScope();
  const router = useRouter();
  const [typed, setTyped] = useState(name);
  const [to, setTo] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const others = scope.clients.filter((c) => c.slug !== slug);

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setError(null);
    setSaved(false);
    try {
      await action();
      scope.reloadClients();
      after?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div className="space-y-4">
      <Field label="Name" value={typed} onChange={setTyped} hint="As it should show, like “Seaside Windows & Doors”. Links stay the same." />
      <Button kind="primary" disabled={!typed.trim() || typed.trim() === name} onClick={() => run(() => api(`clients/${slug}`, { method: "PATCH", json: { name: typed.trim() } }), () => setSaved(true))}>
        Rename
      </Button>
      {saved ? <Alert tone="good">Renamed.</Alert> : null}
      {hasWork ? (
        <div className="space-y-2">
          <p className="text-sm text-muted">To remove {name}, first move its projects and connections to another client (or remove them on their pages).</p>
          {others.length ? (
            <>
              <Select label="Move them to" value={to} onChange={setTo} options={[["", "Choose…"], ...others.map((c): [string, string] => [c.slug, c.name])]} />
              <ConfirmButton
                kind="secondary"
                question={`Move all of ${name}'s projects and connections to ${others.find((c) => c.slug === to)?.name ?? "that client"}, then remove ${name}?`}
                confirm="Move and remove"
                onConfirm={() =>
                  to
                    ? run(async () => {
                        await api(`clients/${slug}/move`, { json: { to } });
                        await api(`clients/${slug}`, { method: "DELETE" });
                      }, () => router.push("/projects"))
                    : setError("Choose the client to move them to.")
                }
              >
                Move them and remove {name}
              </ConfirmButton>
            </>
          ) : null}
        </div>
      ) : (
        <ConfirmButton question={`Remove ${name}? It has no projects or connections.`} confirm={`Remove ${name}`} onConfirm={() => run(() => api(`clients/${slug}`, { method: "DELETE" }), () => router.push("/projects"))}>
          Remove client
        </ConfirmButton>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}
