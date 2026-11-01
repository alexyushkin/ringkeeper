"use client";

import { AGENTS_AND_ROUTINES, AgentDirectory } from "@/components/agents";
import { PageHeader } from "@/components/ui";

/** Every project's agents, narrowed by the client/project switcher. */
export default function AgentsPage() {
  return (
    <>
      <PageHeader title="Agents" subtitle={`Each works for one project, with its own instructions and only the connections you give it. ${AGENTS_AND_ROUTINES}`} />
      <AgentDirectory />
    </>
  );
}
