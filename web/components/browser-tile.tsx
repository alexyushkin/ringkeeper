"use client";

import { useEffect, useState } from "react";

import { ServiceMark } from "@/components/catalog";
import { Alert, Badge, Button, Spinner } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";
import { projectName } from "@/lib/client/scope";

/** GET /connections/browser (services.browser). */
interface BrowserInfo {
  name: string;
  added: boolean;
  projects: string[];
  package: string;
  publisher: string;
  needs: string | null; // what this computer still needs (Node.js), or nothing
  browser: string; // chrome or msedge
  installing: boolean;
}

const BROWSERS: Record<string, string> = { chrome: "Chrome", msedge: "Edge" };

/** What it's for, and how it differs from what tasks can already do on the web. */
export function BrowserAbout({ browser }: { browser?: string }) {
  return (
    <div className="space-y-1 text-sm text-muted">
      <p>
        For pages a task has to use, not just read: clicking through a site, filling in forms, and pages that need a login. It opens {BROWSERS[browser ?? ""] ?? "a browser"} with a profile of its own, not your everyday one, so it&apos;s signed in only where you sign it in.
      </p>
      <p>
        Without it, tasks still search the web and read pages, but only what a page shows to anyone: no clicks, no forms, no logins.
      </p>
      <p>Going to pages and reading them runs by itself. Typing into forms, sending them, and downloads wait for your approval in a client&apos;s project.</p>
    </div>
  );
}

/** Browser (Playwright) on your own tools: one click installs and connects it, linked to no
 * project (or to the one a task asked for), and then you pick. */
export function BrowserTile({ project, onAdded }: { project?: string; onAdded: (name: string) => void }) {
  const info = useApi<BrowserInfo>("connections/browser");
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const going = installing || Boolean(info.data?.installing);
  const reload = info.reload;

  useEffect(() => {
    if (!going) return;
    const timer = setInterval(() => {
      api<{ state: string; error: string | null }>("connections/local/browser")
        .then((job) => {
          if (job.state === "done") {
            setInstalling(false);
            reload();
            onAdded("browser");
          } else if (job.state === "failed") {
            setInstalling(false);
            setError(job.error ?? "The install didn't work.");
            reload();
          }
        })
        .catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [going, reload, onAdded]);

  async function add() {
    setError(null);
    setInstalling(true);
    try {
      await api("connections/browser", { json: { project: project ?? null } });
    } catch (err) {
      setInstalling(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  const data = info.data;
  return (
    <section aria-label="Browser (Playwright)" className="space-y-3 rounded-xl border border-line bg-surface p-3">
      <div className="flex items-center gap-3">
        <ServiceMark id="browser" />
        <h3 className="font-semibold">Browser (Playwright)</h3>
        <Badge tone="good">Built in</Badge>
      </div>
      <BrowserAbout browser={data?.browser} />
      {data?.added ? (
        <p className="text-sm">
          Added{data.projects.length ? `, for ${data.projects.map(projectName).join(", ")}` : ", linked to no project yet"}. Link it to more projects from a project&apos;s Connections tab.
        </p>
      ) : data?.needs ? (
        <Alert tone="warn">It needs {data.needs} on this computer first. Install it, then come back.</Alert>
      ) : going ? (
        <p role="status" className="flex items-center gap-2 text-sm">
          <Spinner /> Installing {data?.package ?? "it"}: a minute or two the first time.
        </p>
      ) : (
        <div className="space-y-1">
          <Button kind="primary" disabled={!data} onClick={() => void add()}>
            Add the browser
          </Button>
          <p className="text-xs text-muted">
            Installs {data?.package ?? "Playwright's server"} from {data?.publisher ?? "Microsoft"}, pinned to that version. {project ? `It's linked to ${projectName(project)}; ` : "It isn't linked to any project: "}you pick which projects can use it next.
          </p>
        </div>
      )}
      {error ? <Alert>{error}</Alert> : null}
    </section>
  );
}
