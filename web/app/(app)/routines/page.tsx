"use client";

import { RoutineList } from "@/components/routines";
import { Alert, Card, PageHeader, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

/** Every project's routines, and pausing them all (the tray's "Pause all routines"). */
export default function RoutinesPage() {
  const settings = useApi<{ values: { routines_paused: boolean; paused: boolean } }>("settings");
  const paused = settings.data?.values.routines_paused ?? false;
  return (
    <>
      <PageHeader title="Routines" subtitle="Agents that run on a schedule, an event, or when you run them. Add one on a project's Routines tab." />
      <Card className="mb-4">
        <Toggle
          label="Pause all routines"
          checked={paused}
          onChange={(on) => void api("settings", { method: "PATCH", json: { routines_paused: on } }).then(settings.reload)}
          hint={paused ? "No routine runs on its own until you turn this off (Test run and Run now still work)." : "Also in the tray. “Stop everything” (Settings) stops all work, not only routines."}
        />
      </Card>
      {settings.data?.values.paused ? <Alert>Everything is stopped (Settings): routines don&apos;t run either.</Alert> : null}
      <RoutineList />
    </>
  );
}
