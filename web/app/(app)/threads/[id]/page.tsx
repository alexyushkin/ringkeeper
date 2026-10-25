"use client";

import { use } from "react";

import { ThreadView } from "@/components/views/thread-view";

export default function ThreadPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <ThreadView id={id} />;
}
