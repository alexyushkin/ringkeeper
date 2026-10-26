"use client";

// A client's page by query string (/client?name=...), in both apps.
import { useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { ClientView } from "@/components/views/client-view";

function FromQuery() {
  const name = useSearchParams().get("name") ?? "";
  return <ClientView name={name} />;
}

export default function ClientByQuery() {
  return (
    <Suspense>
      <FromQuery />
    </Suspense>
  );
}
