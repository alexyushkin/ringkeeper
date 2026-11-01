"use client";

// By query string (/routine?id=...), in both apps: the desktop app's files are
// exported ahead of time, and can't have a page per id.
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { RoutineView } from "@/components/views/routine-view";

function FromQuery() {
  const id = useSearchParams().get("id") ?? "";
  return <RoutineView id={id} />;
}

export default function RoutineByQuery() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
