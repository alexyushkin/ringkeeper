"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Catalog } from "@/components/catalog";
import { ConnectionAdded } from "@/components/connection-added";
import { GoogleConnect } from "@/components/google";
import { SHARED_OWNER, type Kind } from "@/components/shared-tools";
import { Button, Card, Field, PageHeader, Select } from "@/components/ui";
import { clientHref } from "@/lib/client/routes";
import { clientKey, clientName, useScope } from "@/lib/client/scope";

const NEW = "__new__";

const KINDS: [Kind, string, string][] = [
  ["client", "A client's account", "Their database, their n8n, their Gmail. It lands in that client's library, and only their projects can use it."],
  ["shared", "Your own tool", "A browser, files on this computer, a web-page reader, public documentation. It holds no client's data, so any client's projects can use it, or all of them."],
];

export default function NewConnectionPage() {
  return (
    <Suspense>
      <NewConnection />
    </Suspense>
  );
}

/** Where Claude's own connectors stand: they don't carry over. */
function ClaudeNote() {
  return (
    <p className="mb-4 rounded-xl bg-sunken p-3 text-sm text-muted">
      Using these in Claude? Claude&apos;s connectors stay in your Claude account; connect them here once per client.
    </p>
  );
}

/**
 * Browse connectors. Adding one starts with what it is: a client's account, or your
 * own tool that any client's projects can share. Only a client's account asks whose
 * it is. `?kind=shared` (or `client`) and `?client=<slug>` skip those questions;
 * `?google=1` opens the Google account form.
 */
function NewConnection() {
  const router = useRouter();
  const scope = useScope();
  const search = useSearchParams();
  const asked = search.get("kind");
  const google = search.get("google") === "1"; // from Settings › Google app
  const clients = scope.clients;
  const [kind, setKind] = useState<Kind | null>(asked === "shared" || asked === "client" ? asked : search.get("client") ? "client" : null);
  const [picked, setPicked] = useState(search.get("client") ?? scope.client);
  const [typed, setTyped] = useState("");
  const [client, setClient] = useState<string | null>(search.get("client"));
  const [added, setAdded] = useState<string | null>(null);
  const chosen = picked === NEW ? typed.trim() : picked; // a new client, named as typed

  if (kind === null) {
    return (
      <>
        <PageHeader title="Browse connectors" />
        <ClaudeNote />
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
        <ClaudeNote />
        <Card className="space-y-3">
          <Select label="Client" value={picked} onChange={setPicked} options={[["", "Choose…"], ...clients.map((c): [string, string] => [c.slug, c.name]), [NEW, "A new client…"]]} />
          {picked === NEW ? <Field label="Client's name" value={typed} onChange={setTyped} placeholder="General Informatics LLC" /> : null}
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
  const title = shared ? "Add your own tool" : `Add a connection for ${clientName(owner)}`;
  if (added) {
    return (
      <>
        <PageHeader title={title} />
        <ConnectionAdded
          name={added}
          onAnother={() => setAdded(null)}
          onDone={() => router.push(shared ? "/connections" : clientHref(clientKey(owner)))}
          doneLabel={shared ? "Open your tools" : `Open ${clientName(owner)}'s connections`}
        />
      </>
    );
  }
  const done = (name: string) => {
    setAdded(name);
    scope.reloadClients(); // a new client is one now
  };
  return (
    <>
      <PageHeader title={title} subtitle={shared ? "Yours, for any client's projects. Anything not marked read-only waits for your approval." : "Only this client's projects can use it."} />
      <ClaudeNote />
      <Card className="space-y-4">
        {google ? (
          <>
            <h2 className="font-semibold">Add a Google account</h2>
            <GoogleConnect owner={shared ? undefined : owner} shared={shared} onAdded={done} />
          </>
        ) : (
          <Catalog client={owner} kind={kind} onAdded={done} />
        )}
        <Button kind="ghost" onClick={() => (shared ? setKind(null) : setClient(null))}>
          {shared ? "‹ Back" : "‹ Another client"}
        </Button>
      </Card>
    </>
  );
}
