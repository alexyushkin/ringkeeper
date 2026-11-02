"use client";

import { AGENTS_AND_ROUTINES, AgentDirectory } from "@/components/agents";
import { HelpHint } from "@/components/help-hint";
import { PageHeader } from "@/components/ui";

/** Every project's agents, narrowed by the client/project switcher. */
export default function AgentsPage() {
  return (
    <>
      <PageHeader
        title="Agents"
        subtitle={`Each works for one project, with its own instructions and only the connections you give it. ${AGENTS_AND_ROUTINES}`}
        help={<HelpHint doc="agents-and-routines">An agent is a named helper for one project. It follows its instructions every time and can only use the connections you tick. Ask it by name in a request, or let a routine run it.</HelpHint>}
      />
      <AgentDirectory />
    </>
  );
}
