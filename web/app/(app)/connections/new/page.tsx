"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Catalog } from "@/components/catalog";
import { ConnectionForm } from "@/components/forms";
import { Button, Card, Details, Field, PageHeader, Select } from "@/components/ui";
import { useApi } from "@/lib/client/api";
import { clientHref } from "@/lib/client/routes";
import { useScope } from "@/lib/client/scope";
import { slugify } from "@/lib/client/words";

const NEW = "__new__";

/** Adding a connection starts with whose it is: it lands in that client's library. */
export default function NewConnectionPage() {
  const router = useRouter();
  const scope = useScope();
  const projects = useApi<{ owner: string }[]>("projects");
  const clients = [...new Set((projects.data ?? []).map((p) => p.owner))].sort((a, b) => a.localeCompare(b));
  const [picked, setPicked] = useState(scope.client);
  const [typed, setTyped] = useState("");
  const [client, setClient] = useState<string | null>(null);
  const chosen = picked === NEW ? slugify(typed) || typed.trim() : picked;

  if (client === null) {
    return (
      <>
        <PageHeader title="Add a connection" subtitle="First, whose is it? Only that client's projects can use it." />
        <Card className="space-y-3">
          <Select label="Client" value={picked} onChange={setPicked} options={[["", "Choose…"], ...clients.map((c): [string, string] => [c, c]), [NEW, "A new client…"]]} />
          {picked === NEW ? <Field label="Client's name" value={typed} onChange={setTyped} placeholder="Acme Inc." /> : null}
          <Button kind="primary" disabled={!chosen} onClick={() => setClient(chosen)}>
            Next
          </Button>
        </Card>
      </>
    );
  }
  const done = () => router.push(clientHref(client));
  return (
    <>
      <PageHeader title={`Add a connection for ${client}`} />
      <Card className="space-y-4">
        <h2 className="font-semibold">Connect a service</h2>
        <Catalog client={client} onAdded={done} />
        <Details label="Other services (a database, n8n, Twilio, Gmail…)">
          <ConnectionForm client={client} onSaved={done} />
        </Details>
        <Button kind="ghost" onClick={() => setClient(null)}>
          ‹ Another client
        </Button>
      </Card>
    </>
  );
}
