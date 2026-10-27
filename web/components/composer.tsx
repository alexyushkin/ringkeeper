"use client";

import { useRef, useState } from "react";

import { Alert, Button, Spinner } from "@/components/ui";
import { VoiceSetup, useLocalVoice } from "@/components/voice";
import { api } from "@/lib/client/api";

type Phase = "idle" | "recording" | "transcribing" | "heard";

/**
 * Type a request, or hold the mic to speak one. A recording is sent to be
 * transcribed and shown as "I heard: ..."; nothing is sent until you press Send.
 * The recording itself only lives in memory, here and on the server.
 */
export function Composer({ threadId, onSent }: { threadId?: string; onSent: (thread: string) => void }) {
  const [text, setText] = useState("");
  const [heard, setHeard] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const voice = useLocalVoice();
  const [setup, setSetup] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const started = useRef(0);

  async function send(body: string, input: "text" | "voice") {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ thread_id: string }>("messages", {
        json: { body, source: "web", input, author: "owner", thread_id: threadId ?? null },
      });
      setText("");
      setHeard(null);
      setPhase("idle");
      onSent(result.thread_id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function startRecording() {
    setError(null);
    if (!voice.ready) {
      setSetup(true); // first use on this computer: the model comes first
      return;
    }
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setError("This browser can't record here. Voice needs the app's https:// address.");
      return;
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const media = new MediaRecorder(stream);
      chunks.current = [];
      media.ondataavailable = (event) => {
        if (event.data.size) chunks.current.push(event.data);
      };
      media.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        void transcribe(new Blob(chunks.current, { type: media.mimeType || "audio/webm" }));
        chunks.current = [];
      };
      recorder.current = media;
      started.current = Date.now();
      media.start();
      setPhase("recording");
    } catch {
      setError("Ringkeeper needs permission to use the microphone.");
    }
  }

  function stopRecording() {
    if (recorder.current?.state === "recording") {
      if (Date.now() - started.current < 600) {
        // A tap, not a hold: throw it away.
        recorder.current.onstop = () => recorder.current?.stream.getTracks().forEach((t) => t.stop());
        recorder.current.stop();
        setPhase("idle");
        setError("Hold the button while you speak.");
        return;
      }
      recorder.current.stop();
      setPhase("transcribing");
    }
  }

  async function transcribe(audio: Blob) {
    try {
      const result = await api<{ transcript: string }>("voice/transcribe", {
        body: audio,
        contentType: audio.type.split(";")[0] || "audio/webm",
      });
      setHeard(result.transcript);
      setPhase("heard");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPhase("idle");
    }
  }

  if (phase === "heard" && heard !== null) {
    return (
      <div className="space-y-3 rounded-2xl border border-line bg-surface p-3">
        <p className="text-sm">
          <span className="text-muted">I heard:</span> {heard}
        </p>
        {error ? <Alert>{error}</Alert> : null}
        <div className="flex flex-wrap gap-2">
          <Button kind="primary" busy={busy} onClick={() => send(heard, "voice")}>
            Send
          </Button>
          <Button onClick={() => { setHeard(null); setPhase("idle"); }}>Redo</Button>
          <Button kind="ghost" onClick={() => { setText(heard); setHeard(null); setPhase("idle"); }}>
            Edit as text
          </Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="space-y-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim()) void send(text.trim(), "text");
      }}
    >
      <div className="flex items-end gap-2 rounded-2xl border border-line bg-surface p-2 focus-within:border-accent">
        <label htmlFor="composer" className="sr-only">
          Request
        </label>
        <textarea
          id="composer"
          rows={2}
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={threadId ? "Add to this conversation…" : "Ask for something, e.g. “Add a line to acme-web's notes”"}
          className="min-h-11 flex-1 resize-none bg-transparent px-2 py-1 text-base outline-none"
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && text.trim()) void send(text.trim(), "text");
          }}
        />
        {(
          <button
            type="button"
            aria-label={phase === "recording" ? "Recording: release to stop" : "Hold to talk"}
            onPointerDown={(event) => {
              event.preventDefault();
              void startRecording();
            }}
            onPointerUp={stopRecording}
            onPointerLeave={stopRecording}
            onContextMenu={(event) => event.preventDefault()}
            className={`grid size-11 shrink-0 touch-none place-items-center rounded-full ${phase === "recording" ? "animate-pulse bg-bad text-white" : "bg-sunken text-ink"}`}
          >
            {phase === "transcribing" ? (
              <Spinner />
            ) : (
              <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
                <rect x="9" y="3" width="6" height="11" rx="3" />
                <path d="M5 11a7 7 0 0 0 14 0M12 18v3" strokeLinecap="round" />
              </svg>
            )}
          </button>
        )}
        <Button type="submit" kind="primary" busy={busy} disabled={!text.trim()} className="shrink-0">
          Send
        </Button>
      </div>
      {setup && !voice.ready ? <VoiceSetup status={voice.status} error={voice.error} onDownload={() => void voice.download()} onClose={() => setSetup(false)} /> : null}
      <p className="px-1 text-xs text-muted">
        {phase === "recording" ? "Listening… release to stop." : phase === "transcribing" ? "Writing down what you said…" : "Hold the mic to speak. You'll see what I heard before anything is sent."}
      </p>
      {error ? <Alert>{error}</Alert> : null}
    </form>
  );
}
