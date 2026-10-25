"use client";

// The same page by query string (/project?name=...): the desktop app's
// files are exported ahead of time, and can't have a page per name.
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ProjectView } from "@/components/views/project-view";

function FromQuery() {
  const name = useSearchParams().get("name") ?? "";
  return <ProjectView name={name} />;
}

export default function ProjectByQuery() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
