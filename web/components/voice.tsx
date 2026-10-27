"use client";

import { useEffect, useState } from "react";

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
}

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

/** Settings › Voice: Fast or Accuracy, each with its download size shown before it starts. */
export function VoiceSettings() {
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
