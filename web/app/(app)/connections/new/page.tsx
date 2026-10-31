"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { ConnectionForm } from "@/components/forms";
import { SHARED_OWNER, type Kind } from "@/components/shared-tools";
import { Button, Card, Details, Field, PageHeader, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { useScope } from "@/lib/client/scope";
import { slugify } from "@/lib/client/words";

const NEW = "__new__";

const KINDS: [Kind, string, string][] = [
  ["client", "A client's account", "Their database, their n8n, their Gmail. It lands in that client's library, and only their projects can use it."],
  ["shared", "Your own tool", "A browser, files on this computer, a fetcher, a public docs server. It holds no client's data, so any client's projects can use it, or all of them."],
];

export default function NewConnectionPage() {
  return (
    <Suspense>
      <NewConnection />
    </Suspense>
  );
}

/**
 * Adding a connection starts with what it is: a client's account, or your own tool
 * that any client's projects can share. Only a client's account asks whose it is.
 * `?kind=shared` (or `client`) skips the first question.
 */
function NewConnection() {
  const router = useRouter();
  const scope = useScope();
  const asked = useSearchParams().get("kind");
  const projects = useApi<{ owner: string }[]>("projects");
  const clients = [...new Set((projects.data ?? []).map((p) => p.owner))].sort((a, b) => a.localeCompare(b));
  const [kind, setKind] = useState<Kind | null>(asked === "shared" || asked === "client" ? asked : null);
  const [picked, setPicked] = useState(scope.client);
  const [typed, setTyped] = useState("");
  const [client, setClient] = useState<string | null>(null);
  const [added, setAdded] = useState<string | null>(null);
  const chosen = picked === NEW ? slugify(typed) || typed.trim() : picked;

  if (kind === null) {
    return (
      <>
        <PageHeader title="Add a connection" />
        <Card className="space-y-3">
          <h2 className="font-semibold">A client&apos;s account or your own tool?</h2>
          <div className="grid gap-2 sm:grid-cols-2">
            {KINDS.map(([key, label, help]) => (
              <button key={key} type="button" onClick={() => setKind(key)} className="rounded-xl border border-line bg-surface p-3 text-left hover:border-accent">
                <span className="block font-medium">{label}</span>
                <span className="mt-1 block text-sm text-muted">{help}</span>
              </button>
            ))}
          </div>
        </Card>
      </>
    );
  }
  if (kind === "client" && client === null) {
    return (
      <>
        <PageHeader title="Add a client's connection" subtitle="Whose is it? Only that client's projects can use it." />
        <Card className="space-y-3">
          <Select label="Client" value={picked} onChange={setPicked} options={[["", "Choose…"], ...clients.map((c): [string, string] => [c, c]), [NEW, "A new client…"]]} />
          {picked === NEW ? <Field label="Client's name" value={typed} onChange={setTyped} placeholder="Acme Inc." /> : null}
          <div className="flex flex-wrap gap-2">
            <Button kind="primary" disabled={!chosen} onClick={() => setClient(chosen)}>
              Next
            </Button>
            <Button kind="ghost" onClick={() => setKind(null)}>
              ‹ Back
            </Button>
          </div>
        </Card>
      </>
    );
  }
  const shared = kind === "shared";
  const owner = shared ? SHARED_OWNER : (client ?? "");
  const title = shared ? "Add a shared tool" : `Add a connection for ${owner}`;
  if (added) {
    return (
      <>
        <PageHeader title={title} />
        <ConnectionAdded
          name={added}
          onAnother={() => setAdded(null)}
          onDone={() => router.push(shared ? "/connections" : clientHref(owner))}
          doneLabel={shared ? "Open your shared tools" : `Open ${owner}'s connections`}
        />
      </>
    );
  }
  const done = (name: string) => setAdded(name);
  return (
    <>
      <PageHeader title={title} subtitle={shared ? "Yours, for any client's projects. Anything not marked read-only waits for your approval." : undefined} />
      <Card className="space-y-4">
        <h2 className="font-semibold">{shared ? "Find a tool" : "Connect a service"}</h2>
        <Catalog client={owner} kind={kind} onAdded={done} />
        {shared ? null : (
          <Details label="Other services (a database, n8n, Twilio, Gmail…)">
            <ConnectionForm client={owner} onSaved={(c) => done(c.name)} />
          </Details>
        )}
        <Button kind="ghost" onClick={() => (shared ? setKind(null) : setClient(null))}>
          {shared ? "‹ Back" : "‹ Another client"}
        </Button>
      </Card>
    </>
  );
}
