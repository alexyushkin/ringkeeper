"use client";

import Link from "next/link";

import { Alert, Badge, Empty, LinkButton, Loading, PageHeader } from "@/components/ui";
import { useApi } from "@/lib/client/api";
import { AUTONOMY } from "@/lib/client/words";

interface Project {
  name: string;
  owner: string;
  description: string;
  autonomy: string;
}

export default function ProjectsPage() {
  const projects = useApi<Project[]>("projects");
  const byClient = new Map<string, Project[]>();
  for (const project of projects.data ?? []) {
    byClient.set(project.owner, [...(byClient.get(project.owner) ?? []), project]);
  }
  return (
    <>
      <PageHeader title="Clients & projects" subtitle="Each project belongs to one client and has its own connections." action={<LinkButton href="/projects/new" kind="primary">Add</LinkButton>} />
      {projects.error ? <Alert>{projects.error}</Alert> : null}
      {projects.loading && !projects.data ? <Loading /> : null}
      {projects.data?.length === 0 ? <Empty>No projects yet.</Empty> : null}
      {[...byClient.entries()].map(([client, list]) => (
        <section key={client} className="mb-6">
          <h2 className="mb-2 text-sm font-semibold text-muted">{client}</h2>
          <ul className="space-y-2">
            {list.map((project) => (
              <li key={project.name}>
                <Link href={`/projects/${project.name}`} className="block rounded-2xl border border-line bg-surface p-4 hover:border-accent">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{project.name}</span>
                    <Badge>{AUTONOMY[project.autonomy]?.[0] ?? project.autonomy}</Badge>
                  </div>
                  {project.description ? <p className="mt-1 text-sm text-muted">{project.description}</p> : null}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
