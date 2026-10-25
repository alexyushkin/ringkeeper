"use client";

import { use } from "react";

import { TaskView } from "@/components/views/task-view";

export default function TaskPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  return <TaskView id={id} />;
}
