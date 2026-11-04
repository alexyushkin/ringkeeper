"use client";

import Link from "@/components/link";
import { useState } from "react";

import { Alert, Badge, Button, Card, Empty, Field, LinkButton, Loading, PageHeader } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { AUTONOMY } from "@/lib/client/words";
import { clientHref, projectHref } from "@/lib/client/routes";
import { looksLikeSlug, sameClient, useScope, type Client } from "@/lib/client/scope";

interface Project {
  name: string;
  title: string;
  owner: string;
  description: string;
  autonomy: string;
}

/** Every client, also those with only connections or nothing yet, and their projects. */
export default function ProjectsPage() {
  const scope = useScope();
  const projects = useApi<Project[]>("projects");
  const clients = scope.clients;
  const unnamed = clients.filter(looksLikeSlug);
  return (
    <>
      <PageHeader title="Clients & projects" subtitle="Each project belongs to one client and has its own connections." action={<LinkButton href="/projects/new" kind="primary">Add</LinkButton>} />
      {projects.error ? <Alert>{projects.error}</Alert> : null}
      {projects.loading && !projects.data ? <Loading /> : null}
      {unnamed.length ? <NameThem clients={unnamed} onDone={scope.reloadClients} /> : null}
      {projects.data && !clients.length ? <Empty>No clients yet. Add a project, or a connection for a client.</Empty> : null}
      {clients.map((client) => {
        const list = (projects.data ?? []).filter((p) => sameClient(p.owner, client.slug));
        return (
          <section key={client.slug} className="mb-6">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <h2 className="text-sm font-semibold text-muted">{client.name}</h2>
              <Link href={clientHref(client.slug)} className="ml-auto text-sm text-accent">
                {client.connections === 1 ? "1 connection" : `${client.connections} connections`} ›
              </Link>
            </div>
            {list.length ? (
              <ul className="space-y-2">
                {list.map((project) => (
                  <li key={project.name}>
                    <Link href={projectHref(project.name)} className="block rounded-2xl border border-line bg-surface p-4 hover:border-accent">
                      <div className="flex items-center gap-2">
                        <span className="font-medium">{project.title}</span>
                        <Badge>{AUTONOMY[project.autonomy]?.[0] ?? project.autonomy}</Badge>
                      </div>
                      {project.description ? <p className="mt-1 text-sm text-muted">{project.description}</p> : null}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : (
              <div className="flex flex-wrap items-center gap-3 rounded-2xl border border-dashed border-line p-4">
                <span className="text-sm text-muted">No projects yet</span>
                <LinkButton href={`${clientHref(client.slug)}#settings`}>Add project</LinkButton>
              </div>
            )}
          </section>
        );
      })}
    </>
  );
}

/** Clients still named by their slug (from before clients had names): give them
 * their full names, as written. */
function NameThem({ clients, onDone }: { clients: Client[]; onDone: () => void }) {
  const [names, setNames] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const changed = Object.entries(names).filter(([, name]) => name.trim());

  async function save() {
    setBusy(true);
    setError(null);
    try {
      for (const [slug, name] of changed) await api(`clients/${slug}`, { method: "PATCH", json: { name: name.trim() } });
      setNames({});
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mb-6 space-y-3">
      <h2 className="font-semibold">Give your clients their full names</h2>
      <p className="text-sm text-muted">These are named by their short form. Write each the way it should show, like “Acme Windows &amp; Doors”. Links and IDs stay the same.</p>
      {clients.map((client) => (
        <Field key={client.slug} label={client.slug} value={names[client.slug] ?? ""} onChange={(value) => setNames((all) => ({ ...all, [client.slug]: value }))} placeholder={client.slug} />
      ))}
      <Button kind="primary" busy={busy} disabled={!changed.length} onClick={save}>
        Save names
      </Button>
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}
