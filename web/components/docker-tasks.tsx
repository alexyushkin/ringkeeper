"use client";

import { useEffect, useState } from "react";

import { Alert, Button, Section, Toggle } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

type Status = {
  on: boolean;
  available: boolean;
  docker_running: boolean;
  image_ready: boolean;
  size: number | null;
  downloading: boolean;
  downloaded: number;
  total: number;
  error: string | null;
};

const megabytes = (bytes: number) => `${Math.max(1, Math.round(bytes / 1_000_000)).toLocaleString()} MB`;

// Off by default: a task in Docker sees its project's folder and nothing else of this computer.
export function DockerTasks() {
  const status = useApi<Status>("desktop/docker");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = status.reload;
  const s = status.data;
  const watching = Boolean(s?.downloading) || (s !== undefined && s.available && !s.docker_running);

  useEffect(() => {
    if (!watching) return;
    const timer = setInterval(reload, 3000);
    return () => clearInterval(timer);
  }, [watching, reload]);

  async function act(run: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await run();
      reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section id="docker-tasks" title="Run tasks in Docker" summary={s?.on ? "on" : "off"}>
      <div className="space-y-3 text-sm">
        <p>A task in Docker sees only its project&apos;s folder. It can&apos;t reach Ringkeeper&apos;s vault, your keychain or your other files. Without Docker, a task runs as you, on this computer.</p>
        <p className="text-muted">Recommended for client work. If Docker isn&apos;t running when a task starts, the task runs on this computer instead and says so.</p>
        {s && !s.available ? <p className="text-muted">This version of Ringkeeper doesn&apos;t have a Docker image.</p> : null}
        {s?.available && !s.docker_running ? (
          <p>
            Docker Desktop isn&apos;t running. Start it, or{" "}
            <a href="https://www.docker.com/products/docker-desktop/" target="_blank" rel="noopener noreferrer" className="text-accent underline">
              get Docker Desktop
            </a>
            , and this will update.
          </p>
        ) : null}
        {s?.docker_running && !s.image_ready ? (
          s.downloading ? (
            <p>{s.total ? `Downloading: ${megabytes(s.downloaded)} of ${megabytes(s.total)}` : "Starting the download..."}</p>
          ) : (
            <Button busy={busy} onClick={() => act(() => api("desktop/docker/download", { method: "POST" }))}>
              {s.size ? `Download the image (${megabytes(s.size)})` : "Download the image"}
            </Button>
          )
        ) : null}
        {s?.image_ready ? <Toggle label="Run tasks in Docker" checked={s.on} onChange={(on) => act(() => api("desktop/docker", { method: "PUT", json: { on } }))} hint="Takes effect from the next task. The first task in Docker also downloads Claude Code (about 130 MB), once." /> : null}
        {s?.error ? <Alert>{s.error}</Alert> : null}
        {error ? <Alert>{error}</Alert> : null}
      </div>
    </Section>
  );
}
