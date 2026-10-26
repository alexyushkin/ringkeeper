"use client";

import { useRouter } from "next/navigation";

import { ClaudeCodeFolders } from "@/components/claude-folders";
import { ProjectForm } from "@/components/forms";
import { Card, PageHeader } from "@/components/ui";
import { projectHref } from "@/lib/client/routes";

export default function NewProjectPage() {
  const router = useRouter();
  return (
    <>
      <PageHeader title="Add a project" />
      <ClaudeCodeFolders className="mb-4" onAdded={(names) => router.push(names.length === 1 ? projectHref(names[0] ?? "") : "/projects")} />
      <Card>
        <ProjectForm onSaved={(name) => router.push(projectHref(name))} />
      </Card>
    </>
  );
}
