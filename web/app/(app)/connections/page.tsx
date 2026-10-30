"use client";

import Link from "@/components/link";
import { useState } from "react";

import { scopeText, serviceName, type LibraryConnection, type RemoteService } from "@/components/connection-library";
import { SharedTools } from "@/components/shared-tools";
import { Alert, Badge, Empty, EnvBadge, LinkButton, Loading, PageHeader, Section, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { sameClient, useScope } from "@/lib/client/scope";
import { ENVS } from "@/lib/client/words";

/** Every client's connections at a glance. Each client's page holds their library. */
export default function ConnectionsPage() {
  const connections = useApi<LibraryConnection[]>("connections");
  const services = useApi<RemoteService[]>("remote-services");
  const scope = useScope();
  const [service, setService] = useState("");
  const [env, setEnv] = useState("");
  const [attention, setAttention] = useState("");
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
  const clients = [...new Set(shown.map((c) => c.owner))].sort((a, b) => a.localeCompare(b));
  const shared = all.filter((c) => c.shared);
  const sharedSummary = `${shared.length}${shared.some((c) => c.all_projects) ? ` · ${shared.filter((c) => c.all_projects).length} for all projects` : ""}`;
  const clientSummary = (client: string) => {
    const mine = shown.filter((c) => c.owner === client);
    const production = mine.filter((c) => c.env === "prod").length;
    const failing = mine.filter((c) => c.last_test_ok === false).length;
    return [String(mine.length), production ? `${production} production` : "", failing ? `${failing} failing` : ""].filter(Boolean).join(" · ");
  };
  const kinds = [...new Set(all.map(label))].sort();

  return (
    <>
      <PageHeader title="Connections" subtitle="Every client's services. Open a client to add, link or narrow theirs." action={<LinkButton href="/connections/new" kind="primary">Add</LinkButton>} />
      <div className="mb-4 grid grid-cols-1 gap-2 sm:grid-cols-3">
        <Select label="Service" value={service} onChange={setService} options={[["", "Any service"], ...kinds.map((k): [string, string] => [k, k])]} />
        <Select label="Kind of data" value={env} onChange={setEnv} options={[["", "Any"], ...ENVS]} />
        <Select label="Show" value={attention} onChange={setAttention} options={[["", "All"], ["failing", "Last test failed"], ["unlinked", "Not linked to a project"]]} />
      </div>
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      <Section id="shared" title="Your shared tools" summary={sharedSummary}>
        <SharedTools />
      </Section>
      {connections.data && !shown.length ? <Empty>{all.some((c) => !c.shared) ? "Nothing matches." : "No client connections yet."}</Empty> : null}
      {clients.map((client) => (
        <Section
          key={client}
          id={`client-${client}`}
          title={client}
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
                <li key={c.name}>
                  <Link href={clientHref(client)} className="block rounded-2xl border border-line bg-surface p-3 hover:border-accent">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium">{c.name}</span>
                      <EnvBadge env={c.env} />
                      <span className="text-xs text-muted">{label(c)}</span>
                      {c.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
                      {c.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
                    </div>
                    <p className="mt-1 text-xs text-muted">
                      {(c.links ?? []).length
                        ? (c.links ?? []).map((l) => `${l.project} (${scopeText(l.scope, c.scope_fields)})`).join(" · ")
                        : "Not linked to a project yet"}
                    </p>
                  </Link>
                </li>
              ))}
          </ul>
        </Section>
      ))}
    </>
  );
}
