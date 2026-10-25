"use client";

import { useState } from "react";

import { TestButton, type ConnectionData } from "@/components/forms";
import { Alert, Badge, Button, Card, Details, Empty, EnvBadge, Field, LinkButton, Loading, PageHeader, timeAgo } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { SERVICE } from "@/lib/client/words";

export default function ConnectionsPage() {
  const connections = useApi<ConnectionData[]>("connections");
  return (
    <>
      <PageHeader title="Connections" subtitle="Databases and automation tools your projects use. Secrets are stored encrypted and never shown again." action={<LinkButton href="/connections/new" kind="primary">Add</LinkButton>} />
      {connections.error ? <Alert>{connections.error}</Alert> : null}
      {connections.loading && !connections.data ? <Loading /> : null}
      {connections.data?.length === 0 ? <Empty>No connections yet.</Empty> : null}
      <div className="space-y-3">
        {connections.data?.map((connection) => (
          <ConnectionCard key={connection.name} connection={connection} onChange={connections.reload} />
        ))}
      </div>
    </>
  );
}

function ConnectionCard({ connection, onChange }: { connection: ConnectionData; onChange: () => void }) {
  const [secret, setSecret] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  async function replace() {
    setError(null);
    try {
      await api(`connections/${connection.name}/secret`, { method: "PUT", json: { secret } });
      setSecret("");
      setSaved(true);
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function remove() {
    if (!window.confirm(`Remove ${connection.name}? Its stored secret stays unused.`)) return;
    try {
      await api(`connections/${connection.name}`, { method: "DELETE" });
      onChange();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <Card className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{connection.name}</span>
        <EnvBadge env={connection.env} />
        <span className="text-xs text-muted">{SERVICE[connection.type] ?? connection.type}</span>
        {connection.last_test_ok === true ? <Badge tone="good">Works</Badge> : null}
        {connection.last_test_ok === false ? <Badge tone="bad">Last test failed</Badge> : null}
      </div>
      <p className="text-sm text-muted">
        Client {connection.owner}
        {connection.projects.length ? ` · used by ${connection.projects.join(", ")}` : " · not linked to a project yet"}
        {connection.last_test_at ? ` · tested ${timeAgo(connection.last_test_at)}` : ""}
      </p>
      {connection.last_test_ok === false && connection.last_test_message ? <Alert>{connection.last_test_message}</Alert> : null}
      <TestButton name={connection.name} onDone={() => onChange()} />
      <Details label="Change">
        <div className="space-y-3">
          <p className="text-sm">Secret: {connection.secret_stored ? "saved ✓ (hidden)" : "missing"}</p>
          <Field label="New secret" type="password" value={secret} onChange={setSecret} />
          <div className="flex gap-2">
            <Button kind="primary" disabled={!secret} onClick={replace}>
              Replace secret
            </Button>
            <Button kind="danger" onClick={remove}>
              Remove connection
            </Button>
          </div>
          {saved ? <Alert tone="good">Saved. Test the connection to check it.</Alert> : null}
        </div>
      </Details>
      {error ? <Alert>{error}</Alert> : null}
    </Card>
  );
}
