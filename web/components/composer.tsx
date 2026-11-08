"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, Button, Spinner } from "@/components/ui";
import { VoiceSetup, useLocalVoice } from "@/components/voice";
import { api, useApi } from "@/lib/client/api";

type Phase = "idle" | "recording" | "transcribing" | "heard";

const FILL = "rk-fill";

/** Put a request in Home's box, ready to send (from a suggestion or the command
 * palette, maybe before Home is open: it waits until the box is there). Empty text
 * just puts the cursor in it. */
export function fillRequest(text: string): void {
  try {
    sessionStorage.setItem(FILL, text);
  } catch {
    // nothing to do: the event below still reaches an open box
  }
  window.dispatchEvent(new CustomEvent(FILL, { detail: text }));
}

interface Mentionable {
  kind: "client" | "project" | "connection" | "agent";
  token: string;
  label: string;
  detail: string;
}

interface Attached {
  id: string;
  name: string;
}

/** The `@word` being typed at the cursor, if any: where it starts and what's typed. */
function mentionAt(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret);
  const found = /(?:^|\s)@([\w.-]*)$/.exec(before);
  return found ? { start: caret - (found[1] ?? "").length - 1, query: (found[1] ?? "").toLowerCase() } : null;
}

/**
 * Type a request, or hold the mic to speak one. A recording is sent to be
 * transcribed and shown as "I heard: ..."; nothing is sent until you press Send.
 * The recording itself only lives in memory, here and on the server. `@` offers
 * clients, projects, connections and agents: a mention is a firm choice. Files dropped
 * here (or added with the clip) go with the request, into the project its task runs in.
 */
/** An hour from now, on the minute, as a datetime-local value. */
function soon(): string {
  const at = new Date(Date.now() + 3_600_000);
  at.setSeconds(0, 0);
  const local = new Date(at.getTime() - at.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

export function Composer({ threadId, onSent, autoFocus = false, prefix = "" }: { threadId?: string; onSent: (thread: string) => void; autoFocus?: boolean; prefix?: string }) {
  const [text, setText] = useState("");
  const mentionable = useApi<Mentionable[]>("mentions");
  const [mention, setMention] = useState<{ start: number; query: string } | null>(null);
  const [choice, setChoice] = useState(0);
  const [files, setFiles] = useState<Attached[]>([]);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const picker = useRef<HTMLInputElement | null>(null);
  const [heard, setHeard] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [later, setLater] = useState<string | null>(null); // "send later": a datetime-local value
  const voice = useLocalVoice();
  const [setup, setSetup] = useState(false);
  const recorder = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const started = useRef(0);
  const box = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (threadId) return; // only Home's box takes requests from elsewhere
    const take = (value: string | null) => {
      if (value === null) return;
      try {
        sessionStorage.removeItem(FILL);
      } catch {
        // nothing to do
      }
      if (value) setText(value);
      box.current?.focus();
    };
    let waiting: string | null = null;
    try {
      waiting = sessionStorage.getItem(FILL);
    } catch {
      // nothing to do
    }
    take(waiting);
    const listen = (event: Event) => take((event as CustomEvent<string>).detail);
    window.addEventListener(FILL, listen);
    return () => window.removeEventListener(FILL, listen);
  }, [threadId]);

  useEffect(() => {
    if (autoFocus) box.current?.focus();
  }, [autoFocus]);

  const offers = mention
    ? (mentionable.data ?? [])
        .filter((m) => `${m.token} ${m.label}`.toLowerCase().includes(mention.query))
        .slice(0, 8)
    : [];

  function typed(value: string, caret: number) {
    setText(value);
    setMention(mentionAt(value, caret));
    setChoice(0);
  }

  function pickMention(offer: Mentionable | undefined) {
    if (!offer || !mention) return;
    const caret = box.current?.selectionStart ?? text.length;
    const next = `${text.slice(0, mention.start)}@${offer.token} ${text.slice(caret)}`;
    const end = mention.start + offer.token.length + 2;
    setText(next);
    setMention(null);
    requestAnimationFrame(() => {
      box.current?.focus();
      box.current?.setSelectionRange(end, end);
    });
  }

  async function attach(list: FileList | File[]) {
    setUploading(true);
    setError(null);
    try {
      for (const file of Array.from(list)) {
        const saved = await api<Attached>("attachments", {
          method: "POST",
          body: file,
          contentType: "application/octet-stream",
          headers: { "x-file-name": encodeURIComponent(file.name) },
        });
        setFiles((all) => [...all, saved]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
    }
  }

  async function send(body: string, input: "text" | "voice") {
    setBusy(true);
    setError(null);
    try {
      const result = await api<{ thread_id: string }>("messages", {
        json: { body: `${prefix}${body}`, source: "web", input, author: "owner", thread_id: threadId ?? null, attachments: files.map((f) => f.id), run_at: later ? new Date(later).toISOString() : null },
      });
      setLater(null);
      setText("");
      setFiles([]);
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
      data-tour={threadId ? undefined : "ask"}
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim()) void send(text.trim(), "text");
      }}
    >
      <div
        className={`relative flex items-end gap-2 rounded-2xl border bg-surface p-2 focus-within:border-accent ${dragging ? "border-accent bg-accent-soft" : "border-control"}`}
        onDragOver={(event) => {
          event.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(event) => {
          event.preventDefault();
          setDragging(false);
          if (event.dataTransfer.files.length) void attach(event.dataTransfer.files);
        }}
      >
        <label htmlFor="composer" className="sr-only">
          Request
        </label>
        {offers.length ? (
          <ul role="listbox" aria-label="Mention" className="absolute top-full left-2 z-20 mt-1 w-80 max-w-[85vw] overflow-hidden rounded-xl border border-line bg-surface p-1 shadow-lg">
            {offers.map((offer, n) => (
              <li
                key={`${offer.kind}:${offer.token}`}
                role="option"
                aria-selected={n === choice}
                onMouseEnter={() => setChoice(n)}
                onMouseDown={(event) => {
                  event.preventDefault();
                  pickMention(offer);
                }}
                className={`flex cursor-pointer items-baseline gap-2 rounded-lg px-2 py-1.5 text-sm ${n === choice ? "bg-accent-soft" : ""}`}
              >
                <span className="min-w-0 flex-1 truncate">{offer.label}</span>
                <span className="shrink-0 text-xs text-muted">{offer.kind === "project" || offer.kind === "connection" ? offer.detail : offer.kind === "client" ? "client" : offer.detail}</span>
              </li>
            ))}
          </ul>
        ) : null}
        <textarea
          id="composer"
          ref={box}
          rows={2}
          value={text}
          onChange={(event) => typed(event.target.value, event.target.selectionStart)}
          onBlur={() => setMention(null)}
          placeholder={threadId ? "Add to this conversation…" : "Ask for something, e.g. “Add a line to the website's notes”. @ names a client, project or connection."}
          className="min-h-11 flex-1 resize-none bg-transparent px-2 py-1 text-base outline-none"
          onKeyDown={(event) => {
            if (offers.length) {
              if (event.key === "ArrowDown" || event.key === "ArrowUp") {
                event.preventDefault();
                setChoice((n) => (n + (event.key === "ArrowDown" ? 1 : offers.length - 1)) % offers.length);
                return;
              }
              if (event.key === "Enter" || event.key === "Tab") {
                event.preventDefault();
                pickMention(offers[choice]);
                return;
              }
              if (event.key === "Escape") {
                event.preventDefault();
                setMention(null);
                return;
              }
            }
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && text.trim()) void send(text.trim(), "text");
          }}
        />
        <input
          ref={picker}
          type="file"
          multiple
          aria-label="Attach files"
          className="hidden"
          onChange={(event) => {
            if (event.target.files?.length) void attach(event.target.files);
            event.target.value = "";
          }}
        />
        <button type="button" aria-label="Attach files" title="Attach files" onClick={() => picker.current?.click()} className="grid size-11 shrink-0 place-items-center rounded-full bg-sunken text-ink">
          {uploading ? (
            <Spinner />
          ) : (
            <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
              <path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.5 3.5 0 0 1 5 5l-8.6 8.6a2 2 0 0 1-2.8-2.8l7.9-7.9" />
            </svg>
          )}
        </button>
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
        <Button type="submit" kind="primary" busy={busy} disabled={!text.trim() || later === ""} className="shrink-0">
          {later ? "Schedule" : "Send"}
        </Button>
      </div>
      {threadId ? null : later === null ? (
        <button type="button" className="self-start px-1 text-xs text-accent" onClick={() => setLater(soon())}>
          Send later…
        </button>
      ) : (
        <div className="flex flex-wrap items-end gap-2 px-1">
          <label className="text-sm">
            <span className="mb-1 block text-xs text-muted">Run it at (this computer&apos;s time)</span>
            <input type="datetime-local" value={later} min={soon()} onChange={(event) => setLater(event.target.value)} className="rounded-lg border border-line bg-surface px-2 py-1" />
          </label>
          <Button kind="ghost" onClick={() => setLater(null)}>
            Send now instead
          </Button>
          <p className="w-full text-xs text-muted">It waits until then, and you can cancel it on Home before it runs. If Ringkeeper isn&apos;t running then, it runs when Ringkeeper starts, unless that&apos;s more than 6 hours late.</p>
        </div>
      )}
      {files.length ? (
        <ul className="flex flex-wrap gap-2 px-1" aria-label="Attached files">
          {files.map((file) => (
            <li key={file.id} className="flex items-center gap-1 rounded-full border border-line px-3 py-1 text-sm">
              {file.name}
              <button type="button" className="ml-1 text-muted hover:text-ink" aria-label={`Remove ${file.name}`} onClick={() => setFiles((all) => all.filter((f) => f.id !== file.id))}>
                ×
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      {setup && !voice.ready ? <VoiceSetup status={voice.status} error={voice.error} onDownload={() => void voice.download()} onClose={() => setSetup(false)} /> : null}
      <p className="px-1 text-xs text-muted">
        {phase === "recording" ? "Listening… release to stop." : phase === "transcribing" ? "Writing down what you said…" : "Hold the mic to speak, or drop files here. You'll see what I heard before anything is sent."}
      </p>
      {error ? <Alert>{error}</Alert> : null}
    </form>
  );
}
