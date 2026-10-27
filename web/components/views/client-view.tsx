"use client";

import Link from "@/components/link";
import { useState } from "react";

import { LibraryCard, useLibrary } from "@/components/connection-library";
import { Catalog } from "@/components/catalog";
import { ConnectionForm } from "@/components/forms";
import { Alert, Button, Card, Details, Empty, Loading } from "@/components/ui";
import { projectHref } from "@/lib/client/routes";

/** One client: their projects and their connection library. A connection is added
 * once and linked to any of their projects, each link narrowed if it needs to be. */
export function ClientView({ name }: { name: string }) {
  const library = useLibrary(name);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);

  function done(connection: string) {
    setAdded(connection);
    setAdding(false);
    library.reload();
  }

  return (
    <>
      <Link href="/projects" className="text-sm text-accent">
        ‹ Clients & projects
      </Link>
      <h1 className="mt-2 mb-1 text-xl font-semibold">{name}</h1>
      <p className="mb-4 text-sm text-muted">
        Projects:{" "}
        {library.projects.length
          ? library.projects.map((p, i) => (
              <span key={p}>
                {i ? ", " : ""}
                <Link href={projectHref(p)} className="text-accent">
                  {p}
                </Link>
              </span>
            ))
          : "none yet"}
      </p>

      <Card className="mb-4 space-y-3">
        <div className="flex items-center gap-2">
          <h2 className="font-semibold">Connections</h2>
          <span className="ml-auto" />
          {!adding ? (
            <Button kind="primary" onClick={() => setAdding(true)}>
              Add a connection
            </Button>
          ) : null}
        </div>
        <p className="text-sm text-muted">
          {name}&apos;s services. Connect each one once, then link it to any of {name}&apos;s projects; a link can be narrowed to one database, base or project. Other clients&apos; projects can&apos;t use these.
        </p>
        {added ? <Alert tone="good">Added {added}. Link it to a project below, and test it.</Alert> : null}
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
        <div className="space-y-3">
          {library.connections.map((connection) => (
            <LibraryCard key={connection.name} connection={connection} projects={library.projects} services={library.services} onChange={library.reload} />
          ))}
        </div>
      </Card>
    </>
  );
}
