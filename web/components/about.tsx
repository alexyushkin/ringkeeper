"use client";

import { useState } from "react";

import { Button, Card, Details } from "@/components/ui";
import { api, useApi } from "@/lib/client/api";

interface Component {
  name: string;
  version: string;
  license: string;
  kind: string;
}

interface About {
  version: string;
  mode: string;
  components: Component[];
  notices: boolean;
}

/** Settings > About: the version, and what Ringkeeper includes under which license. */
export function AboutCard() {
  const about = useApi<About>("about");
  const [text, setText] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const data = about.data;
  if (!data) return null;

  const showNotices = () => {
    setBusy(true);
    api<{ text: string }>("about/notices")
      .then((result) => setText(result.text))
      .finally(() => setBusy(false));
  };
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">About</h2>
      <p className="text-sm">
        Ringkeeper {data.version}
        <span className="text-muted"> · {data.mode === "desktop" ? "desktop app" : "server"} · MIT License</span>
      </p>
      <p className="text-xs text-muted">Copyright (c) 2026 General Informatics LLC and contributors</p>
      {data.notices ? (
        <>
          <p className="text-sm text-muted">
            Ringkeeper includes {data.components.length} third-party components, among them Python, Git for Windows (MinGit),
            the Claude Code CLI (Anthropic&apos;s, under its Commercial Terms) and Microsoft&apos;s WebView2 Runtime.
          </p>
          <Details label="Show the components">
            <ul className="max-h-72 space-y-1 overflow-y-auto text-xs">
              {data.components.map((c) => (
                <li key={`${c.kind}:${c.name}:${c.version}`}>
                  <span className="font-medium">{c.name}</span> {c.version} <span className="text-muted">· {c.license} · {c.kind}</span>
                </li>
              ))}
            </ul>
          </Details>
          {text === null ? (
            <Button busy={busy} onClick={showNotices}>
              Show the license notices
            </Button>
          ) : (
            <pre className="max-h-96 overflow-auto rounded-xl bg-sunken p-3 text-xs whitespace-pre-wrap">{text}</pre>
          )}
        </>
      ) : null}
    </Card>
  );
}
