"use client";

import Link from "next/link";
import { useState } from "react";

import { ConnectionForm, TestButton, type ConnectionData } from "@/components/forms";
import { Alert, Card, PageHeader } from "@/components/ui";

export default function NewConnectionPage() {
  const [added, setAdded] = useState<ConnectionData | null>(null);
  return (
    <>
      <PageHeader title="Add a connection" />
      <Card className="space-y-4">
        {added ? (
          <>
            <Alert tone="good">Added {added.name}. Its secret is stored encrypted and won't be shown again.</Alert>
            <TestButton name={added.name} />
            <p className="text-sm">
              Next, link it to a project on the{" "}
              <Link href="/projects" className="text-accent">
                project's page
              </Link>
              .
            </p>
          </>
        ) : (
          <ConnectionForm onSaved={setAdded} />
        )}
      </Card>
    </>
  );
}
