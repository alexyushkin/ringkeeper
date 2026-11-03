"use client";

import { useEffect, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Alert, Button, Card, Field, LinkButton } from "@/components/ui";
import { api } from "@/lib/client/api";

export interface HelpAnswer {
  text: string;
  cites: { title: string; url: string }[];
  actions: { label: string; href?: string; url?: string }[];
}

/** Where an answer came from in the guide, and the next step. A button only opens a
 * page: what you do there goes through the page as usual. */
export function HelpExtras({ help }: { help: HelpAnswer }) {
  return (
    <div className="mt-2 space-y-2">
      {help.cites.length ? (
        <p className="text-xs text-muted">
          From the guide:{" "}
          {help.cites.map((cite, n) => (
            <span key={cite.url}>
              {n ? " · " : ""}
              <a href={cite.url} target="_blank" rel="noreferrer" className="text-accent underline">
                {cite.title}
              </a>
            </span>
          ))}
        </p>
      ) : null}
      {help.actions.length ? (
        <div className="flex flex-wrap gap-2">
          {help.actions.map((action) =>
            action.href ? (
              <LinkButton key={action.label} href={action.href}>
                {action.label}
              </LinkButton>
            ) : (
              <a key={action.label} href={action.url} target="_blank" rel="noreferrer" className="inline-flex min-h-11 items-center rounded-xl border border-line px-4 text-sm font-medium hover:border-accent">
                {action.label}
              </a>
            ),
          )}
        </div>
      ) : null}
    </div>
  );
}

/** Help › Ask: how to use Ringkeeper, from the guide and your setup. */
export function AskHelp() {
  const [question, setQuestion] = useState("");
  const [help, setHelp] = useState<HelpAnswer | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const asked = new URLSearchParams(window.location.search).get("ask");
    if (asked) setQuestion(asked);
  }, []);

  async function ask(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    setHelp(null);
    try {
      setHelp(await api<HelpAnswer>("help/ask", { json: { question: question.trim() } }));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card className="mb-4 space-y-3">
      <form onSubmit={ask} className="space-y-3">
        <Field label="Ask how something works" value={question} onChange={setQuestion} placeholder="How do I link a connection to a project?" />
        <Button type="submit" kind="primary" busy={busy} disabled={question.trim().length < 2}>
          Ask
        </Button>
      </form>
      {error ? <Alert>{error}</Alert> : null}
      {help ? (
        <div className="rounded-xl bg-sunken p-3">
          <Markdown text={help.text} className="text-sm" />
          <HelpExtras help={help} />
        </div>
      ) : null}
    </Card>
  );
}
