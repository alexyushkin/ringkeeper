"use client";

import { AskHelp } from "@/components/help-answer";
import { useEffect, useState } from "react";

import { openPalette } from "@/components/command-palette";
import { ReportProblem } from "@/components/report-problem";
import { startTour } from "@/components/tour";
import { Button, Card, PageHeader } from "@/components/ui";
import { DOCS, docUrl } from "@/lib/client/docs";

const CONCEPTS: [string, string, string][] = [
  ["Client", "Someone you do work for, like General Informatics LLC Their projects and connections are kept apart from every other client's.", "concepts"],
  ["Project", "One piece of a client's work: a website, an app, a set of automations. A request goes to one project, and works in its folder.", "concepts"],
  ["Connection", "A client's service a project can use: a database, Airtable, GitHub, a Google account. Ringkeeper keeps the key; the AI never sees it.", "connections"],
  ["Shared tool", "A tool of yours that holds no client's data, like a web browser or files on this computer. You can give it to any client's projects.", "connections"],
  ["Agent", "A named helper in a project, with its own instructions and only the connections you give it. An agent is who does the work.", "agents-and-routines"],
  ["Routine", "When an agent runs on its own: on a schedule, when something happens, or when you press Run. A routine is when it runs.", "agents-and-routines"],
  ["Hold", "A change to live client data, or a message to real people, waits for your OK in Approvals. Nothing happens until you approve it.", "approvals"],
];

/** Help: the seven ideas in plain words, a picture of how they fit, the guide, and
 * where to report a problem. */
export default function HelpPage() {
  const [reporting, setReporting] = useState(false);
  // ?report=1: the command palette's Report a problem.
  useEffect(() => setReporting(new URLSearchParams(window.location.search).has("report")), []);
  return (
    <>
      {reporting ? (
        <div className="mb-4">
          <ReportProblem onClose={() => setReporting(false)} />
        </div>
      ) : null}
      <PageHeader title="Help" subtitle="How Ringkeeper fits together, in seven ideas." />
      <AskHelp />
      <Card className="mb-4">
        <Diagram />
      </Card>
      <dl className="mb-6 space-y-3">
        {CONCEPTS.map(([name, text, doc]) => (
          <div key={name} className="rounded-2xl border border-line bg-surface p-4">
            <dt className="font-semibold">{name}</dt>
            <dd className="mt-1 text-sm">
              {text}{" "}
              <a href={docUrl(doc)} target="_blank" rel="noreferrer" className="text-accent underline">
                More
              </a>
            </dd>
          </div>
        ))}
      </dl>
      <Card className="space-y-3">
        <h2 className="font-semibold">More help</h2>
        <ul className="space-y-2 text-sm">
          <li>
            <a href={`${DOCS}/README.md`} target="_blank" rel="noreferrer" className="text-accent underline">
              The user guide
            </a>{" "}
            — setting up each service, approvals and undo, backups, and what to do when something goes wrong.
          </li>
          <li>
            <a href={docUrl("troubleshooting")} target="_blank" rel="noreferrer" className="text-accent underline">
              Something isn&apos;t working
            </a>{" "}
            — common problems and their fixes.
          </li>
          <li>
            Press <kbd className="rounded border border-line px-1 text-xs">Ctrl K</kbd> anywhere to jump to a page, a client, a project or a setting.
          </li>
        </ul>
        <div className="flex flex-wrap gap-2">
          <Button onClick={startTour}>Show the tour again</Button>
          <Button onClick={openPalette}>Open the command palette</Button>
          <Button
            onClick={() => {
              setReporting(true);
              window.scrollTo({ top: 0 });
            }}
          >
            Report a problem
          </Button>
        </div>
      </Card>
    </>
  );
}

function Box({ x, y, w, title, text, tone = "plain" }: { x: number; y: number; w: number; title: string; text: string; tone?: "plain" | "warn" | "accent" }) {
  const stroke = tone === "warn" ? "var(--color-warn)" : tone === "accent" ? "var(--color-accent)" : "var(--color-line)";
  return (
    <g>
      <rect x={x} y={y} width={w} height={52} rx={10} fill="var(--color-surface)" stroke={stroke} strokeWidth={1.5} />
      <text x={x + 12} y={y + 21} fontSize={13} fontWeight={600} fill="var(--color-ink)">
        {title}
      </text>
      <text x={x + 12} y={y + 40} fontSize={11.5} fill="var(--color-muted)">
        {text}
      </text>
    </g>
  );
}

function Arrow({ d, label, lx, ly }: { d: string; label?: string; lx?: number; ly?: number }) {
  return (
    <g>
      <path d={d} fill="none" stroke="var(--color-muted)" strokeWidth={1.4} markerEnd="url(#help-arrow)" />
      {label ? (
        <text x={lx} y={ly} fontSize={11} fill="var(--color-muted)">
          {label}
        </text>
      ) : null}
    </g>
  );
}

/** Client → project → its connections and shared tools; an agent in the project, run
 * by a routine; a change to live data held for you. */
function Diagram() {
  return (
    <svg viewBox="0 0 640 312" role="img" aria-labelledby="help-diagram-title" className="h-auto w-full">
      <title id="help-diagram-title">
        A client has projects. A project uses the client&apos;s connections and your shared tools. An agent works in a project and a routine runs it. A change to live data is held until you approve it.
      </title>
      <defs>
        <marker id="help-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
          <path d="M0 0L10 5L0 10z" fill="var(--color-muted)" />
        </marker>
      </defs>
      <Box x={10} y={14} w={180} title="Client" text="General Informatics LLC" />
      <Box x={10} y={104} w={180} title="Project" text="geninfos-web" tone="accent" />
      <Box x={250} y={60} w={180} title="Connections" text="their database, Airtable" />
      <Box x={450} y={60} w={180} title="Shared tools" text="your browser, your files" />
      <Box x={10} y={200} w={180} title="Agent" text="follow-up: who does it" />
      <Box x={250} y={200} w={180} title="Routine" text="weekdays at 9: when" />
      <Box x={450} y={200} w={180} title="Hold" text="waits for your OK" tone="warn" />
      <Arrow d="M100 66 V100" label="has" lx={108} ly={88} />
      <Arrow d="M190 124 C220 124 220 86 246 86" label="uses" lx={200} ly={104} />
      <Arrow d="M190 136 C330 160 470 150 534 116" />
      <Arrow d="M100 156 V196" label="has" lx={108} ly={182} />
      <Arrow d="M250 226 H194" label="runs" lx={204} ly={220} />
      <Arrow d="M100 252 C100 290 540 290 540 256" label="a change to live data" lx={262} ly={305} />
    </svg>
  );
}
