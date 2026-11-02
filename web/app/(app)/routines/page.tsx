"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { AGENTS_AND_ROUTINES } from "@/components/agents";
import { HelpHint } from "@/components/help-hint";
import { NewRoutineAnywhere, RoutineList } from "@/components/routines";
import { Alert, Button, Card, PageHeader, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { routineHref } from "@/lib/client/routes";
import { useScope } from "@/lib/client/scope";

/** Every project's routines (narrowed by the switcher), adding one, and pausing them all
 * (the tray's "Pause all routines"). */
export default function RoutinesPage() {
  const router = useRouter();
  const scope = useScope();
  const settings = useApi<{ values: { routines_paused: boolean; paused: boolean } }>("settings");
  const paused = settings.data?.values.routines_paused ?? false;
  // ?new=1: the command palette's New routine.
  const [adding, setAdding] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => setAdding(new URLSearchParams(window.location.search).has("new")), []);
  return (
    <>
      <PageHeader
        title="Routines"
        subtitle={`Run an agent on a schedule, when something happens, or when you say. ${AGENTS_AND_ROUTINES}`}
        help={<HelpHint doc="agents-and-routines">A routine starts one of a project&apos;s agents: on a schedule, when something happens (a new email, a file, a webhook), or when you press Run. Each run is a task; changes still wait for your approval.</HelpHint>}
        action={
          adding ? null : (
            <Button kind="primary" onClick={() => setAdding(true)}>
              New routine
            </Button>
          )
        }
      />
      {adding ? (
        <div className="mb-4">
          <NewRoutineAnywhere onCreated={(id) => router.push(routineHref(id))} onCancel={() => setAdding(false)} />
        </div>
      ) : null}
      <Card className="mb-4">
        <Toggle
          label="Pause all routines"
          checked={paused}
          onChange={(on) => void api("settings", { method: "PATCH", json: { routines_paused: on } }).then(settings.reload, () => setFailed(true))}
          hint={paused ? "No routine runs on its own until you turn this off (Test run and Run now still work)." : "Also in the tray. “Stop everything” (Settings) stops all work, not only routines."}
        />
      </Card>
      {failed ? <Alert>Couldn&apos;t change that. Try again in a moment.</Alert> : null}
      {settings.data?.values.paused ? <Alert>Everything is stopped, so routines don&apos;t run either. Press Resume in Settings to start again.</Alert> : null}
      <RoutineList project={scope.project || undefined} client={scope.client || undefined} />
    </>
  );
}
