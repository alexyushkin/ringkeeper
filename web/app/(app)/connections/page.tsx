"use client";

import Link from "@/components/link";
import { useState } from "react";

import { ServiceMark } from "@/components/catalog";
import { connectionTitle } from "@/components/connection-form";
import { FoundLogins } from "@/components/conveniences";
import { markOf, planLimit, scopeText, serviceName, type LibraryConnection, type RemoteService } from "@/components/connection-library";
import { HelpHint } from "@/components/help-hint";
import { ScopeNote } from "@/components/scope-switcher";
import { SharedTools } from "@/components/shared-tools";
import { Alert, Badge, Button, Empty, EnvBadge, LinkButton, Loading, PageHeader, Section, Select } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { clientName, place, projectName, sameClient, useScope } from "@/lib/client/scope";
import { ENVS } from "@/lib/client/words";

const KINDS_OF_DATA: [string, string][] = ENVS.map(([key, label]) => [key, label]);

/** Every client's connections at a glance, and your own tools. Each client's page
 * holds their library; Browse connectors adds one. */
export default function ConnectionsPage() {
  const connections = useApi<LibraryConnection[]>("connections");
  const services = useApi<RemoteService[]>("remote-services");
  const scope = useScope();
  const [service, setService] = useState("");
  const [env, setEnv] = useState("");
  const [attention, setAttention] = useState("");
  const [testing, setTesting] = useState(false);
  const all = connections.data ?? [];
  const label = (c: LibraryConnection) => serviceName(c, services.data ?? []);
  const shown = all.filter(
    (c) =>
      !c.shared &&
      (!scope.client || sameClient(c.owner, scope.client)) &&
      (!scope.project || c.projects.includes(scope.project)) &&
      (!service || label(c) === service) &&
      (!env || c.env === env) &&
      (!attention || (attention === "failing" ? c.last_test_ok === false : c.projects.length === 0)),
  );
  const clients = [...new Set(shown.map((c) => c.owner))].sort((a, b) => clientName(a).localeCompare(clientName(b)));
  const shared = all.filter((c) => c.shared);
  const sharedSummary = `${shared.length}${shared.some((c) => c.all_projects) ? ` · ${shared.filter((c) => c.all_projects).length} for all projects` : ""}`;
  const clientSummary = (client: string) => {
    const mine = shown.filter((c) => c.owner === client);
    const production = mine.filter((c) => c.env === "prod").length;
    const failing = mine.filter((c) => c.last_test_ok === false).length;
    const unlinked = mine.filter((c) => !c.projects.length).length;
    return [`${mine.length} connection${mine.length === 1 ? "" : "s"}`, production ? `${production} production` : "", failing ? `${failing} failing` : "", unlinked ? `${unlinked} not linked` : ""].filter(Boolean).join(" · ");
  };
  const kinds = [...new Set(all.map(label))].sort();

  async function testAll() {
    setTesting(true);
    await api("connections/test-all", { method: "POST" }).catch(() => undefined);
    // Results land on the cards as each test finishes.
    setTimeout(() => {
      connections.reload();
      setTesting(false);
    }, 4000);
  }

  return (
    <>
      <PageHeader
        title="Connectors"
        subtitle="Every client's connected services, and your own tools. Open a client to link or narrow theirs."
        action={
          <div className="flex flex-wrap gap-2">
            <Button onClick={testAll} busy={testing}>
              Test all
            </Button>
            <LinkButton href="/connections/new" kind="primary">
              Browse connectors
            </LinkButton>
          </div>
        }
        help={<HelpHint doc="connections">A connection is a client&apos;s service (a database, Airtable, GitHub…) that their projects can use. Your own tool holds no client&apos;s data and can go to any project. Ringkeeper keeps every key; the AI never sees one.</HelpHint>}
      />
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Select label="Service" value={service} onChange={setService} options={[["", "Any service"], ...kinds.map((k): [string, string] => [k, k])]} />
        <Select label="Kind of data" value={env} onChange={setEnv} options={[["", "Any"], ...KINDS_OF_DATA]} />
        <Select label="Show" value={attention} onChange={setAttention} options={[["", "All"], ["failing", "Last test failed"], ["unlinked", "Not linked to a project"]]} />
      </div>
      <ScopeNote />
      <div className="mb-4">
        <FoundLogins onChanged={connections.reload} />
      </div>
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      <Section id="shared" title="Your own tools" summary={sharedSummary} action={<LinkButton href="/connections/new?kind=shared">Add your own tool</LinkButton>}>
        <SharedTools />
      </Section>
      {connections.data && !shown.length ? (
        <Empty>
          {all.some((c) => !c.shared) ? (
            service || env || attention ? "Nothing matches. Set the filters back to Any." : `${place(scope.client, scope.project)} has no connections of its own.`
          ) : (
            <>
              No client connections yet. A connection is a client&apos;s service, like their database or Airtable.{" "}
              <Link href="/connections/new?kind=client" className="text-accent underline">
                Connect a client&apos;s service
              </Link>
              .
            </>
          )}
        </Empty>
      ) : null}
      {clients.map((client) => (
        <Section
          key={client}
          id={`client-${client}`}
          title={clientName(client)}
          summary={clientSummary(client)}
          action={
            <Link href={clientHref(client)} className="text-sm text-accent">
              Open ›
            </Link>
          }
        >
          <ul className="space-y-2">
            {shown
              .filter((c) => c.owner === client)
              .map((c) => (
                <li key={c.name} className="rounded-2xl border border-line bg-surface p-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <ServiceMark id={markOf(c)} database={c.type === "supabase"} />
                    <Link href={`${clientHref(client)}#connections/${encodeURIComponent(c.name)}`} className="font-medium hover:text-accent">
                      {connectionTitle(c)}
                    </Link>
                    {c.type === "google" ? null : <EnvBadge env={c.env} />}
                    {label(c) !== connectionTitle(c) ? <span className="text-xs text-muted">{label(c)}</span> : null}
                    {c.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
                    {planLimit(c) ? <Badge tone="warn">Plan limit</Badge> : c.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
                  </div>
                  {planLimit(c) ? <p className="mt-1 text-sm text-warn">{planLimit(c)}</p> : null}
                  {c.last_test_ok === false && c.last_test_message ? <p className="mt-1 text-sm text-bad">{c.last_test_message}</p> : null}
                  <p className="mt-1 text-sm">
                    {(c.links ?? []).length ? (
                      <span className="text-muted">Used by {(c.links ?? []).map((l) => (Object.keys(l.scope).length ? `${projectName(l.project)} (${scopeText(l.scope, c.scope_fields)})` : projectName(l.project))).join(", ")}</span>
                    ) : (
                      <>
                        <span className="text-muted">Not linked yet · </span>
                        <Link href={`${clientHref(client)}#connections/${encodeURIComponent(c.name)}/link`} className="text-accent">
                          Link to a project ›
                        </Link>
                      </>
                    )}
                  </p>
                </li>
              ))}
          </ul>
        </Section>
      ))}
    </>
  );
}
