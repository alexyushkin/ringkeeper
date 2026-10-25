"use client";

// The same page by query string (/thread?id=...): the desktop app's
// files are exported ahead of time, and can't have a page per id.
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ThreadView } from "@/components/views/thread-view";

function FromQuery() {
  const id = useSearchParams().get("id") ?? "";
  return <ThreadView id={id} />;
}

export default function ThreadByQuery() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
