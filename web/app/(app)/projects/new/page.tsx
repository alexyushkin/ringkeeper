"use client";

import { useRouter } from "next/navigation";

import { ProjectForm } from "@/components/forms";
import { Card, PageHeader } from "@/components/ui";

export default function NewProjectPage() {
  const router = useRouter();
  return (
    <>
      <PageHeader title="Add a project" />
      <Card>
        <ProjectForm onSaved={(name) => router.push(`/projects/${name}`)} />
      </Card>
    </>
  );
}
