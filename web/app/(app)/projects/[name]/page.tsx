"use client";

import { use } from "react";

import { ProjectView } from "@/components/views/project-view";

export default function ProjectPage({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  return <ProjectView name={name} />;
}
