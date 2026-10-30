"use client";

import Link from "@/components/link";
import { useState } from "react";

import { LibraryCard, useLibrary } from "@/components/connection-library";
import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { ConnectionForm, ProjectForm } from "@/components/forms";
import { ScopedTasks, useTaskSummary } from "@/components/scoped-tasks";
import { Alert, Button, Details, Empty, Loading, Section, Tabs, type Tab } from "@/components/ui";
import { projectHref } from "@/lib/client/routes";

/** One client: their projects and their connection library. A connection is added
 * once and linked to any of their projects, each link narrowed if it needs to be. */
export function ClientView({ name }: { name: string }) {
  const library = useLibrary(name);
  const tasks = useTaskSummary({ client: name });
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);

  function done(connection: string) {
    setAdded(connection);
    setAdding(false);
    library.reload();
  }

  const production = library.connections.filter((c) => c.env === "prod").length;
  const summary = `${library.connections.length}${production ? ` · ${production} production` : ""}`;

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
                  <span className="ml-2 text-xs text-muted">{library.connections.filter((c) => c.projects.includes(p)).length} of {name}&apos;s connections</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No projects for {name} yet (Settings tab).</Empty>
          )}
        </Section>
      ),
    },
    {
      key: "connections",
      label: "Connections",
      count: library.connections.length,
      content: () => (
        <Section
          id="connections"
          title="Connections"
          summary={summary}
          action={
            !adding && !added ? (
              <Button kind="primary" onClick={() => setAdding(true)}>
                Add a connection
              </Button>
            ) : null
          }
        >
          <p className="text-sm text-muted">
            {name}&apos;s services. Connect each one once, then link it to any of {name}&apos;s projects; a link can be narrowed to one database, base or project. Other clients&apos; projects can&apos;t use these.
          </p>
          {added ? (
            <ConnectionAdded
              name={added}
              onAnother={() => {
                setAdded(null);
                setAdding(true);
              }}
              onDone={() => {
                setAdded(null);
                library.reload();
              }}
            />
          ) : null}
          {adding ? (
            <div className="space-y-4 rounded-xl border border-line p-3">
              <h3 className="font-medium">Connect a service</h3>
              <Catalog client={name} onAdded={done} />
              <Details label="Other services (a database, n8n, Twilio, Gmail…)">
                <ConnectionForm client={name} onSaved={(c) => done(c.name)} />
              </Details>
              <Button kind="ghost" onClick={() => setAdding(false)}>
                Cancel
              </Button>
            </div>
          ) : null}
          {library.error ? <Alert>{library.error}</Alert> : null}
          {library.loading && !library.connections.length ? <Loading /> : null}
          {!library.loading && !library.connections.length ? <Empty>No connections for {name} yet.</Empty> : null}
          <div className="space-y-2">
            {library.connections.map((connection) => (
              <LibraryCard key={connection.name} connection={connection} projects={library.projects} services={library.services} onChange={library.reload} compact />
            ))}
          </div>
        </Section>
      ),
    },
    { key: "tasks", label: "Tasks", count: tasks.count, content: () => <ScopedTasks client={name} /> },
    {
      key: "settings",
      label: "Settings",
      content: () => (
        <Section id="new-project" title={`Add a project for ${name}`} defaultOpen={!library.projects.length}>
          <ProjectForm client={name} onSaved={() => library.reload()} />
        </Section>
      ),
    },
  ];

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-1 text-xl font-semibold">{name}</h1>
      <p className="mb-4 text-sm text-muted">
        {library.projects.length} project{library.projects.length === 1 ? "" : "s"} · {summary} connection{library.connections.length === 1 ? "" : "s"}
        {tasks.running ? ` · ${tasks.running} running` : ""}
      </p>
      <Tabs id={`client-${name}`} tabs={tabs} />
    </>
  );
}
