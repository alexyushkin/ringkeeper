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

/** Auto-detect, or one of the speech model's languages, found by typing its name. */
function LanguagePicker({ value, languages, onChange }: { value: string; languages: Record<string, string>; onChange: (code: string) => void }) {
  const [query, setQuery] = useState("");
  const id = useId();
  const all: [string, string][] = [[AUTO, "Auto-detect"], ...Object.entries(languages).sort(([, a], [, b]) => a.localeCompare(b))];
  const wanted = query.trim().toLowerCase();
  const found = wanted ? all.filter(([code, name]) => code === wanted || name.toLowerCase().includes(wanted)) : all;
  // The current choice stays in the list, so the list always shows what's chosen.
  const shown = found.some(([code]) => code === value) ? found : [...all.filter(([code]) => code === value), ...found];
  return (
    <div className="space-y-1">
      <label htmlFor={id} className="block text-sm font-medium">
        Language
      </label>
      <input
        id={id}
        type="search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="Search languages"
        aria-controls={`${id}-list`}
        className="min-h-11 w-full rounded-xl border border-line bg-surface px-3 text-base"
      />
      <select
        id={`${id}-list`}
        aria-label="Language"
        size={6}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="w-full rounded-xl border border-line bg-surface px-1 py-1 text-base"
      >
        {shown.map(([code, name]) => (
          <option key={code} value={code} className="rounded-lg px-2 py-1">
            {name}
          </option>
        ))}
      </select>
      <p className="text-xs text-muted">The language you speak. Choosing it is faster and makes fewer mistakes on short recordings than auto-detect.</p>
    </div>
  );
}

/** Settings › Voice: Fast or Accuracy, each with its download size shown before it
 * starts, and the language spoken (saved as stt_language). */
export function VoiceSettings({ language, onLanguage }: { language: string; onLanguage: (code: string) => void }) {
  const voice = useLocalVoice();
  const status = voice.status;
  if (!status?.engine) return null;
  const downloading = status.state === "downloading";
  const anyReady = status.choices.some((c) => c.ready);
  const size = (c: VoiceChoice) => (c.ready ? "downloaded" : `${megabytes(c.download)} download`);
  return (
    <Card className="mb-4 space-y-3">
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
      {status.choice === "fast" && language !== "en" ? (
        <Alert tone="warn">
          <span>
            The Fast model is weak outside English.{" "}
            {language === AUTO ? "If you speak other languages, choose Accuracy." : `For ${status.languages[language] ?? language}, choose Accuracy.`}
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
