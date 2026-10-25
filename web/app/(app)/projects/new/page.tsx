"use client";

import { useRouter } from "next/navigation";

import { ProjectForm } from "@/components/forms";
import { Card, PageHeader } from "@/components/ui";
import { projectHref } from "@/lib/client/routes";

export default function NewProjectPage() {
  const router = useRouter();
  return (
    <>
      <PageHeader title="Add a project" />
      <Card>
        <ProjectForm onSaved={(name) => router.push(projectHref(name))} />
      </Card>
    </>
  );
}
