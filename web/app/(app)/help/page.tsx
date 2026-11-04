"use client";

import { AskHelp } from "@/components/help-answer";
import { HOW_IT_WORKS } from "@/components/how-it-works";
import { useEffect, useState } from "react";

import { openPalette } from "@/components/command-palette";
import { ReportProblem } from "@/components/report-problem";
import { startTour } from "@/components/tour";
import { Button, Card, PageHeader } from "@/components/ui";
import { DOCS, docUrl } from "@/lib/client/docs";

const CONCEPTS: [string, string, string][] = [
  ["Request", "What you ask, in your own words: typed or spoken, in the app or on Telegram. This is how nearly everything starts, and it's all you need.", "concepts"],
  ["Client", "Someone you do work for, like General Informatics LLC Their projects and connections are kept apart from every other client's.", "concepts"],
  ["Project", "One piece of a client's work: a website, an app, a set of automations. A request goes to one project, and works in its folder.", "concepts"],
  ["Connection", "A client's service a project can use: a database, Airtable, GitHub, a Google account. Ringkeeper keeps the key; the AI never sees it.", "connections"],
  ["Shared tool", "A tool of yours that holds no client's data, like a web browser or files on this computer. You can give it to any client's projects.", "connections"],
  ["Agent", "Optional. A named helper in a project, with its own instructions and only the connections you give it. An agent is who does the work.", "agents-and-routines"],
  ["Routine", "Optional. When an agent runs on its own: on a schedule, when something happens, or when you press Run. A routine is when it runs.", "agents-and-routines"],
  ["Hold", "A change to live client data, or a message to real people, waits for your OK in Approvals. Nothing happens until you approve it.", "approvals"],
];

/** Help: the eight ideas in plain words, a picture of how they fit, the guide, and
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
      <PageHeader title="Help" subtitle="How Ringkeeper fits together, in eight ideas." />
      <AskHelp />
      {/* Our own drawing, from scripts/how_it_works.py; wide, so a phone scrolls it. */}
      <div className="mb-4 overflow-x-auto rounded-3xl">
        <div className="min-w-[760px]" dangerouslySetInnerHTML={{ __html: HOW_IT_WORKS }} />
      </div>
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
