"use client";

import { useEffect, useId, useState } from "react";

import { Alert, Button, Card, Select } from "@/components/ui";
import { api } from "@/lib/client/api";
import { DESKTOP } from "@/lib/client/routes";

interface VoiceChoice {
  key: string;
  label: string;
  model: string;
  ready: boolean;
  download: number | null; // bytes to fetch if chosen now
}

export interface LocalVoice {
  engine: boolean;
  state: "absent" | "downloading" | "ready" | "failed";
  total: number | null;
  done: number;
  error: string | null;
  choice: string;
  choices: VoiceChoice[];
  languages: Record<string, string>; // the speech model's codes, by name
}

export const AUTO = "auto";

const DESCRIPTIONS: Record<string, string> = {
  fast: "quick, good for short requests in English",
  accuracy: "slower and a bigger download, better with accents, names and other languages",
};

export function megabytes(bytes: number | null): string {
  return bytes ? `${Math.round(bytes / 1_000_000)} MB` : "a few hundred MB";
}

/**
 * The desktop app's voice: speech is turned into text on this computer, so its
 * engine and model are downloaded once, when the mic is first used. The audio never leaves.
 */
export function useLocalVoice() {
  const [status, setStatus] = useState<LocalVoice | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () =>
    api<LocalVoice>("desktop/voice")
      .then(setStatus)
      .catch((err: unknown) => setError(err instanceof Error ? err.message : String(err)));
  useEffect(() => {
    if (DESKTOP) void refresh();
  }, []);
  useEffect(() => {
    if (status?.state !== "downloading") return;
    const timer = setInterval(() => void refresh(), 1000);
    return () => clearInterval(timer);
  }, [status?.state]);
  async function run(request: Promise<LocalVoice | void>) {
    setError(null);
    try {
      const result = await request;
      if (result) setStatus(result);
      else await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }
  return {
    status,
    error,
    ready: !DESKTOP || status?.state === "ready",
    download: () => run(api<LocalVoice>("desktop/voice/download", { method: "POST" })),
    choose: (choice: string) => run(api<LocalVoice>("desktop/voice/choice", { method: "PUT", json: { choice } })),
    remove: () => run(api<void>("desktop/voice", { method: "DELETE" })),
  };
}

function Progress({ status }: { status: LocalVoice }) {
  const share = status.total ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;
  return (
    <div className="space-y-1">
      <div className="h-2 overflow-hidden rounded-full bg-sunken" role="progressbar" aria-valuenow={share} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full bg-accent transition-all" style={{ width: `${share}%` }} />
      </div>
      <p className="text-xs text-muted">
        {megabytes(status.done)} of {megabytes(status.total)} ({share}%)
      </p>
    </div>
  );
}

/** Shown in the request box the first time the mic is used. */
export function VoiceSetup({ status, error, onDownload, onClose }: { status: LocalVoice | null; error: string | null; onDownload: () => void; onClose: () => void }) {
  if (status && !status.engine) {
    return <Alert tone="warn">Voice isn&apos;t part of this build of Ringkeeper. Type your request instead.</Alert>;
  }
  const chosen = status?.choices.find((c) => c.key === status.choice);
  const other = status?.choices.find((c) => c.key !== status.choice);
  return (
    <div className="space-y-2 rounded-2xl border border-line bg-surface p-3 text-sm">
      <p className="font-medium">Voice on this computer</p>
      <p className="text-muted">
        Ringkeeper turns speech into text right here, so your recordings never leave this computer. That needs the speech engine and the {chosen?.label ?? ""} model, downloaded once ({megabytes(status?.total ?? null)}).
        {other ? ` For ${other.label.toLowerCase()} instead (${megabytes(other.download)}), choose it in Settings › Voice.` : ""}
      </p>
      {status?.state === "downloading" ? (
        <Progress status={status} />
      ) : (
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" onClick={onDownload}>
            Download ({megabytes(status?.total ?? null)})
          </Button>
          <Button kind="ghost" onClick={onClose}>
            Not now
          </Button>
        </div>
      )}
      {status?.error ? <Alert>{status.error}</Alert> : null}
      {error ? <Alert>{error}</Alert> : null}
    </div>
  );
}

// At most this many (the server's MAX_SPOKEN).
const MAX_SPOKEN = 5;

/** The languages chosen, from the saved setting: "auto" (none) or "en,ru". */
export function spokenLanguages(value: string): string[] {
  return value === AUTO ? [] : value.split(",").filter(Boolean);
}

/** The languages you speak: none (any, detected per recording), one, or a few. Each is
 * found by typing its name and added to the ones chosen. */
function LanguagePicker({ value, languages, onChange }: { value: string; languages: Record<string, string>; onChange: (value: string) => void }) {
  const [query, setQuery] = useState("");
  const id = useId();
  const chosen = spokenLanguages(value);
  const save = (codes: string[]) => onChange(codes.length ? codes.join(",") : AUTO);
  const all = Object.entries(languages).sort(([, a], [, b]) => a.localeCompare(b));
  const wanted = query.trim().toLowerCase();
  const shown = all.filter(([code, name]) => !chosen.includes(code) && (!wanted || code === wanted || name.toLowerCase().includes(wanted)));
  const full = chosen.length >= MAX_SPOKEN;
  return (
    <div className="space-y-1">
      <p id={`${id}-label`} className="text-sm font-medium">
        Languages you speak
      </p>
      <ul aria-labelledby={`${id}-label`} className="flex flex-wrap gap-2">
        {chosen.length ? (
          chosen.map((code) => (
            <li key={code} className="flex items-center gap-1 rounded-full bg-sunken py-0.5 pr-1 pl-3 text-sm">
              {languages[code] ?? code}
              <button type="button" onClick={() => save(chosen.filter((c) => c !== code))} aria-label={`Remove ${languages[code] ?? code}`} className="inline-flex size-7 items-center justify-center rounded-full text-muted hover:bg-surface hover:text-ink">
                ×
              </button>
            </li>
          ))
        ) : (
          <li className="text-sm text-muted">Any language, detected in each recording.</li>
        )}
      </ul>
      {full ? null : (
        <>
          <input
            id={id}
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={chosen.length ? "Add another language" : "Search languages"}
            aria-controls={`${id}-list`}
            aria-label="Search languages"
            className="min-h-11 w-full rounded-xl border border-line bg-surface px-3 text-base"
          />
          <select
            id={`${id}-list`}
            aria-label="Add a language"
            size={6}
            value=""
            onChange={(event) => {
              save([...chosen, event.target.value]);
              setQuery("");
            }}
            className="w-full rounded-xl border border-line bg-surface px-1 py-1 text-base"
          >
            {shown.map(([code, name]) => (
              <option key={code} value={code} className="rounded-lg px-2 py-1">
                {name}
              </option>
            ))}
          </select>
        </>
      )}
      <p className="text-xs text-muted">
        One language is fastest and makes the fewest mistakes. With a few, each recording is checked against just those (a moment slower), so it&apos;s never written in a language you don&apos;t speak. None: any language{full ? `. At most ${MAX_SPOKEN}.` : "."}
      </p>
    </div>
  );
}

/** Settings › Voice: Fast or Accuracy, each with its download size shown before it
 * starts, and the languages spoken (saved as stt_language: "auto", or "en,ru"). */
export function VoiceSettings({ language, onLanguage }: { language: string; onLanguage: (value: string) => void }) {
  const voice = useLocalVoice();
  const spoken = spokenLanguages(language);
  const notEnglish = spoken.filter((code) => code !== "en");
  const status = voice.status;
  if (!status?.engine) return null;
  const downloading = status.state === "downloading";
  const anyReady = status.choices.some((c) => c.ready);
  const size = (c: VoiceChoice) => (c.ready ? "downloaded" : `${megabytes(c.download)} download`);
  return (
    <Card anchor="voice" className="mb-4 space-y-3">
      <h2 className="font-semibold">Voice</h2>
      <p className="text-sm text-muted">Speech becomes text on this computer; recordings never leave it. Only the chosen model is kept.</p>
      <Select
        label="Speech model"
        value={status.choice}
        onChange={(choice) => void voice.choose(choice)}
        options={status.choices.map((c) => [c.key, `${c.label}: ${DESCRIPTIONS[c.key] ?? c.model} (${size(c)})`])}
        hint={downloading ? "You can switch once the download is done." : undefined}
      />
      <LanguagePicker value={language} languages={status.languages} onChange={onLanguage} />
      {status.choice === "fast" && (!spoken.length || notEnglish.length) ? (
        <Alert tone="warn">
          <span>
            The Fast model is weak outside English.{" "}
            {spoken.length ? `For ${notEnglish.map((code) => status.languages[code] ?? code).join(" and ")}, choose Accuracy.` : "If you speak other languages, choose Accuracy."}
          </span>{" "}
          {downloading ? null : (
            <Button kind="ghost" onClick={() => void voice.choose("accuracy")}>
              Use Accuracy
            </Button>
          )}
        </Alert>
      ) : null}
      {downloading ? (
        <Progress status={status} />
      ) : status.state === "ready" ? (
        <p className="text-sm">Ready.</p>
      ) : (
        <Button kind="primary" onClick={() => void voice.download()}>
          Download ({megabytes(status.total)})
        </Button>
      )}
      {anyReady && !downloading ? (
        <Button kind="ghost" onClick={() => void voice.remove()}>
          Remove voice from this computer
        </Button>
      ) : null}
      {status.error ? <Alert>{status.error}</Alert> : null}
      {voice.error ? <Alert>{voice.error}</Alert> : null}
    </Card>
  );
}
