"use client";

import Link from "@/components/link";
import { useState } from "react";

import { LibraryCard, NoProjects, useLibrary } from "@/components/connection-library";
import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { connectionTitle } from "@/components/connection-form";
import { ProjectForm } from "@/components/forms";
import { ClientMonth, MonthlyCap, money } from "@/components/client-month";
import { ScopedTasks, useTaskSummary } from "@/components/scoped-tasks";
import { Alert, Badge, Button, ConfirmButton, Empty, Field, Loading, Section, Select, Tabs, Toggle, type Tab } from "@/components/ui";
import { api } from "@/lib/client/api";
import { projectHref } from "@/lib/client/routes";
import { clientName, projectName, sameClient, useScope } from "@/lib/client/scope";
import { useRouter } from "next/navigation";

/** One client: their projects and their connection library. A connection is added
 * once and linked to any of their projects, each link narrowed if it needs to be. */
export function ClientView({ name }: { name: string }) {
  const library = useLibrary(name);
  const display = clientName(name);
  const scope = useScope();
  const client = scope.clients.find((c) => sameClient(c.slug, name));
  const tasks = useTaskSummary({ client: name });
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState<string | null>(null);

  function done(connection: string) {
    setAdded(connection);
    setAdding(false);
    library.reload();
  }

  const everything = [...library.connections, ...library.tools];
  const production = everything.filter((c) => c.env === "prod").length;
  const summary = `${everything.length} connection${everything.length === 1 ? "" : "s"}${production ? ` · ${production} production` : ""}`;

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
                    {projectName(p)}
                  </Link>
                  <span className="ml-2 text-xs text-muted">{library.connections.filter((c) => c.projects.includes(p)).length} of {display}&apos;s connections</span>
                </li>
              ))}
            </ul>
          ) : (
            <NoProjects client={name} />
          )}
          {library.projects.length > 1 ? <DefaultProject slug={name} name={display} projects={library.projects} chosen={client?.default_project ?? null} onSaved={scope.reloadClients} /> : null}
        </Section>
      ),
    },
    {
      key: "connections",
      label: "Connections",
      count: everything.length,
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
          {!library.loading && !everything.length && !adding ? <Empty>No connections for {display} yet. Connect their first service below.</Empty> : null}
          {library.tools.length ? (
            <div className="space-y-2">
              <h3 className="text-sm font-medium">Your own tools {display}&apos;s projects use</h3>
              <ul className="space-y-2">
                {library.tools.map((tool) => (
                  <li key={tool.name} className="rounded-xl border border-line p-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <Link href="/connections#shared" className="font-medium hover:text-accent">
                        {connectionTitle(tool)}
                      </Link>
                      <Badge tone="accent">your tool</Badge>
                    </div>
                    <p className="mt-1 text-sm text-muted">Used by {tool.projects.filter((p) => library.projects.includes(p)).map(projectName).join(", ")}</p>
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
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
    { key: "month", label: "This month", content: () => <ClientMonth slug={name} /> },
    { key: "tasks", label: "Tasks", count: tasks.count, content: () => <ScopedTasks client={name} /> },
    {
      key: "settings",
      label: "Settings",
      content: () => (
        <>
          <Section id="new-project" title={`Add a project for ${display}`} defaultOpen={!library.projects.length}>
            <ProjectForm client={name} onSaved={() => library.reload()} />
          </Section>
          <Section id="own-client" title="Your own work" summary={client?.own ? "this is you" : "no"} defaultOpen={false}>
            <OwnClient slug={name} name={display} own={Boolean(client?.own)} onSaved={scope.reloadClients} />
          </Section>
          <Section id="monthly-cap" title="Monthly cap" summary={client?.cap_usd ? money(client.cap_usd) : "none"} defaultOpen={false}>
            <MonthlyCap slug={name} name={display} cap={client?.cap_usd ?? null} onSaved={scope.reloadClients} />
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
        {library.projects.length} project{library.projects.length === 1 ? "" : "s"} · {everything.length} connection{everything.length === 1 ? "" : "s"}
        {production ? ` (${production} production)` : ""}
        {tasks.running ? ` · ${tasks.running} running` : ""}
        {client?.month_usd ? ` · ${money(client.month_usd)} this month${client.cap_usd ? ` of ${money(client.cap_usd)}` : ""}` : ""}
      </p>
      <Tabs id={`client-${name}`} tabs={tabs} />
    </>
  );
}

/** The operator's own client: work that needs no client's connection (a web lookup,
 * writing, a summary) runs in its default project when nothing is chosen at the top. */
export function OwnClient({ slug, name, own, onSaved }: { slug: string; name: string; own: boolean; onSaved: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function save(on: boolean) {
    setError(null);
    try {
      await api(`clients/${slug}/own`, { method: "PUT", json: { own: on } });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="space-y-2">
      <Toggle
        label={`${name} is me`}
        checked={own}
        onChange={(on) => void save(on)}
        hint="Work that needs no client's connection, like a web lookup, writing or a summary, runs in this client's default project when nothing is chosen at the top. Only one client can be you."
      />
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

/** Where a request for this client goes when it needs no particular project: its email,
 * Google or Slack. With one project, that one; with several, the one chosen here. */
export function DefaultProject({ slug, name, projects, chosen, onSaved }: { slug: string; name: string; projects: string[]; chosen: string | null; onSaved: () => void }) {
  const [error, setError] = useState<string | null>(null);
  async function save(project: string) {
    setError(null);
    try {
      await api(`clients/${slug}/default-project`, { method: "PUT", json: { project: project || null } });
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return (
    <div className="mt-3 space-y-1">
      <Select
        label="Default project"
        value={chosen ?? ""}
        onChange={(value) => void save(value)}
        options={[["", "None: ask me"], ...projects.map((p): [string, string] => [p, projectName(p)])]}
      />
      <p className="text-xs text-muted">Where a request for {name} goes when any project would do, like reading their email or Google. Work on one project&apos;s site, code or data still asks if it&apos;s unclear which.</p>
      {error ? <Alert>{error}</Alert> : null}
    </div>
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
      <Field label="Name" value={typed} onChange={setTyped} hint="As it should show, like “Acme Windows & Doors”. Links stay the same." />
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
