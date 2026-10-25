"use client";

// The same page by query string (/task?id=...): the desktop app's
// files are exported ahead of time, and can't have a page per id.
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { TaskView } from "@/components/views/task-view";

function FromQuery() {
  const id = useSearchParams().get("id") ?? "";
  return <TaskView id={id} />;
}

export default function TaskByQuery() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
