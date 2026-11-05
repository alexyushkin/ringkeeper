"use client";

import { useEffect, useRef, useState } from "react";

import { Alert, Button, Card } from "@/components/ui";
import { megabytes } from "@/components/voice";
import { api } from "@/lib/client/api";

interface LocalOcr {
  available: boolean;
  state: "absent" | "downloading" | "ready" | "failed";
  total: number;
  done: number;
  error: string | null;
  offer: boolean; // a task has met a scan it couldn't read, and nobody has answered yet
  needs_macos: number | null;
}

const LOOK_EVERY_MS = 30_000;
const WHILE_DOWNLOADING_MS = 1_000;

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

/** The scan reader: optional, like voice, and read on this computer. */
function useLocalOcr() {
  const [status, setStatus] = useState<LocalOcr | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = () =>
    api<LocalOcr>("desktop/ocr")
      .then(setStatus)
      .catch((err: unknown) => setError(errorText(err)));
  const downloading = status?.state === "downloading";
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => void refresh(), downloading ? WHILE_DOWNLOADING_MS : LOOK_EVERY_MS);
    return () => clearInterval(timer);
  }, [downloading]);
  async function run(request: Promise<LocalOcr | void>) {
    setError(null);
    try {
      const result = await request;
      if (result) setStatus(result);
      else await refresh();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return {
    status,
    error,
    download: () => run(api<LocalOcr>("desktop/ocr/download", { method: "POST" })),
    decline: () => run(api<LocalOcr>("desktop/ocr/decline", { method: "POST" })),
    remove: () => run(api<void>("desktop/ocr", { method: "DELETE" })),
  };
}

function share(status: LocalOcr): number {
  return status.total ? Math.min(100, Math.round((status.done / status.total) * 100)) : 0;
}

/** Offered once, the first time a task meets a picture or a scan it can't read. */
export function ScanOffer() {
  const ocr = useLocalOcr();
  const offered = useRef(false);
  const [closed, setClosed] = useState(false);
  const status = ocr.status;
  if (status?.offer) offered.current = true;
  if (!status || !offered.current || closed) return null;
  if (!status.offer && status.state !== "ready") return null; // "Not now"
  const button = "rounded-lg bg-white/20 px-3 py-1 font-medium";
  const text =
    status.state === "ready"
      ? "Scans can be read now. Ask the task to read that file again."
      : status.state === "downloading"
        ? `Downloading the scan reader: ${share(status)}%`
        : `A task met a scan it couldn't read. Read scans on this computer? One download (${megabytes(status.total)}), and nothing leaves this computer.`;
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 bg-accent px-4 py-2 text-sm text-white" role="status">
      <span>{ocr.error ?? status.error ?? text}</span>
      {status.state === "ready" ? (
        <button type="button" className={button} onClick={() => setClosed(true)}>
          Close
        </button>
      ) : status.state === "downloading" ? null : (
        <span className="flex gap-2">
          <button type="button" className={button} onClick={() => void ocr.download()}>
            Download
          </button>
          <button type="button" className={button} onClick={() => void ocr.decline()}>
            Not now
          </button>
        </span>
      )}
    </div>
  );
}

/** Settings › Reading scans. */
export function ScanSettings() {
  const ocr = useLocalOcr();
  const status = ocr.status;
  if (!status) return null;
  if (status.needs_macos) {
    return (
      <Card className="mb-4 space-y-3">
        <h2 className="font-semibold">Reading scans</h2>
        <p className="text-sm text-muted">Reading scans needs macOS {status.needs_macos} or later. Tasks can still read PDFs that have text in them.</p>
      </Card>
    );
  }
  if (!status.available) return null;
  return (
    <Card className="mb-4 space-y-3">
      <h2 className="font-semibold">Reading scans</h2>
      <p className="text-sm text-muted">Lets tasks read pictures and scanned PDFs, in English. They&apos;re read on this computer and never leave it.</p>
      {status.state === "downloading" ? (
        <p className="text-sm">Downloading: {share(status)}%</p>
      ) : status.state === "ready" ? (
        <>
          <p className="text-sm">Ready.</p>
          <Button kind="ghost" onClick={() => void ocr.remove()}>
            Remove the scan reader from this computer
          </Button>
        </>
      ) : (
        <Button kind="primary" onClick={() => void ocr.download()}>
          Download ({megabytes(status.total)})
        </Button>
      )}
      {status.error ? <Alert>{status.error}</Alert> : null}
      {ocr.error ? <Alert>{ocr.error}</Alert> : null}
    </Card>
  );
}
