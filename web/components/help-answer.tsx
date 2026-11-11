"use client";

import { useEffect, useRef, useState } from "react";

import { Markdown } from "@/components/markdown";
import { Alert, Button, Card, LinkButton, TextArea } from "@/components/ui";
import { api } from "@/lib/client/api";
import { useSendKeys } from "@/lib/client/use-send-keys";

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

interface Turn {
  question: string;
  help: HelpAnswer;
}

// What the server takes (MAX_EARLIER, and EarlierIn's answer).
const EARLIER_TURNS = 6;
const EARLIER_ANSWER = 4000;

// Here, not in state: an answer's button opens another page, and coming back finds the
// conversation. Nothing is saved, so signing out or reloading ends it.
let kept: Turn[] = [];

/** Help › Ask: how to use Ringkeeper, from the guide and your setup. Ask on from an
 * answer, or start again with New question. */
export function AskHelp() {
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>(kept);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const form = useRef<HTMLFormElement>(null);

  function remember(next: Turn[]) {
    kept = next;
    setTurns(next);
  }

  useEffect(() => {
    const asked = new URLSearchParams(window.location.search).get("ask");
    if (asked) setQuestion(asked);
  }, []);

  const keys = useSendKeys(() => void ask());

  async function ask(event?: React.FormEvent) {
    event?.preventDefault();
    if (question.trim().length < 2 || busy) return;
    setBusy(true);
    setError(null);
    const asked = question.trim();
    const earlier = turns.slice(-EARLIER_TURNS).map((turn) => ({
      question: turn.question,
      answer: turn.help.text.slice(0, EARLIER_ANSWER),
    }));
    try {
      const help = await api<HelpAnswer>("help/ask", { json: { question: asked, earlier } });
      remember([...turns, { question: asked, help }]);
      setQuestion("");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function startAgain() {
    remember([]);
    setQuestion("");
    setError(null);
    form.current?.querySelector("textarea")?.focus();
  }

  return (
    <Card className="mb-4 space-y-3">
      {turns.map((turn, n) => (
        <div key={n} className="space-y-2">
          <p className="text-sm font-medium">{turn.question}</p>
          <div className="rounded-xl bg-sunken p-3">
            <Markdown text={turn.help.text} className="text-sm" />
            <HelpExtras help={turn.help} />
          </div>
        </div>
      ))}
      {error ? <Alert>{error}</Alert> : null}
      <form ref={form} onSubmit={ask} className="space-y-3">
        <TextArea
          label={turns.length ? "Ask a follow-up" : "Ask how something works"}
          value={question}
          onChange={setQuestion}
          rows={2}
          placeholder={turns.length ? "What should I do next?" : "How do I link a connection to a project?"}
          keys={keys}
        />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" kind="primary" busy={busy} disabled={question.trim().length < 2}>
            Ask
          </Button>
          {turns.length ? (
            <Button onClick={startAgain} disabled={busy}>
              New question
            </Button>
          ) : null}
        </div>
      </form>
    </Card>
  );
}
