"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ClaudeCodeFolders } from "@/components/claude-folders";
import { ProjectForm } from "@/components/forms";
import { Card, PageHeader } from "@/components/ui";
import { projectHref } from "@/lib/client/routes";
import { clientName } from "@/lib/client/scope";

export default function NewProjectPage() {
  return (
    <Suspense>
      <NewProject />
    </Suspense>
  );
}

/** From Claude first (what you already work on), then a project by hand. `?client=`
 * fills in whose it is. */
function NewProject() {
  const router = useRouter();
  const client = useSearchParams().get("client") ?? undefined;
  return (
    <>
      <PageHeader title={client ? `Add a project for ${clientName(client)}` : "Add a project"} />
      <ClaudeCodeFolders className="mb-4" onAdded={(names) => router.push(names.length === 1 ? projectHref(names[0] ?? "") : "/projects")} />
      <Card>
        <ProjectForm client={client} onSaved={(name) => router.push(projectHref(name))} />
      </Card>
    </>
  );
}
