/**
 * Documents api client (#6): raw-body upload (one file per request), list, delete, and the
 * German messages for every upload outcome.
 */
import { ApiError, apiFetch } from "@/lib/api";

export const DOCUMENTS_KEY = ["documents"] as const;
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;
export const MAX_PARALLEL_UPLOADS = 3;
export const POLL_MS = 2000;

export type DocumentStatus = "queued" | "processing" | "done" | "needs_attention" | "failed";

export interface DocumentOut {
  id: string;
  status: DocumentStatus;
  original_filename: string | null;
  mime_type: string;
  size_bytes: number;
  channel: "web" | "telegram";
  doc_type: string | null;
  error_kind: string | null;
  attention_reason?: string | null; // #10
  created_at: string;
  updated_at: string;
}

export interface UploadResult {
  document: DocumentOut;
  duplicate: boolean;
  requeued?: boolean;
}

/** `UTF-8''<percent-encoded name>` (RFC 8187), so any name fits an ASCII header. */
export function filenameHeader(name: string): string {
  return `UTF-8''${encodeURIComponent(name)}`;
}

/** POST the `File` itself as the body (streamed by the browser, never read into a string). */
export function uploadDocument(file: File): Promise<UploadResult> {
  return apiFetch<UploadResult>("/documents", {
    method: "POST",
    body: file,
    headers: {
      "Content-Type": "application/octet-stream",
      "X-Filename": filenameHeader(file.name),
    },
  });
}

/** Documents without a tax item (#10): in flight, failed and needs attention without item. */
export async function listDocuments(): Promise<DocumentOut[]> {
  const body = await apiFetch<{ documents: DocumentOut[] }>("/documents?without_tax_item=true");
  return body.documents;
}

export async function deleteDocument(id: string): Promise<void> {
  await apiFetch(`/documents/${encodeURIComponent(id)}`, { method: "DELETE" });
}

export const isRunning = (status: DocumentStatus) => status === "queued" || status === "processing";

/** "resend": the button sends the same `File` again; "pick": it opens the file picker. */
export type RetryMode = "resend" | "pick";

export interface UploadProblem {
  message: string;
  retry: RetryMode;
}

export const MESSAGES = {
  failed: "Upload fehlgeschlagen – bitte erneut hochladen",
  storageFull: "Speicher voll – bitte Bescheid geben und später erneut hochladen",
  tooLarge: "Datei zu groß (max. 25 MB) – bitte eine kleinere Datei hochladen",
  unsupported: "Dateityp nicht unterstützt – bitte ein Bild (kein SVG) oder eine PDF hochladen",
  empty: "Datei ist leer – bitte erneut hochladen",
  processingFailed: "Verarbeitung fehlgeschlagen – bitte erneut hochladen",
  duplicate: "Bereits vorhanden",
  requeued: "Bereits vorhanden – wird erneut verarbeitet",
  busy: "Wird gerade verarbeitet, bitte gleich nochmal versuchen",
} as const;

/** Client-side pre-check (the server enforces the same). */
export function precheck(file: File): UploadProblem | null {
  if (file.size === 0) return { message: MESSAGES.empty, retry: "pick" };
  if (file.size > MAX_UPLOAD_BYTES) return { message: MESSAGES.tooLarge, retry: "pick" };
  return null;
}

/** The message for a failed upload request (network errors are not `ApiError`s). */
export function uploadProblem(error: unknown): UploadProblem {
  if (!(error instanceof ApiError)) return { message: MESSAGES.failed, retry: "resend" };
  switch (error.status) {
    case 413:
      return { message: MESSAGES.tooLarge, retry: "pick" };
    case 415:
      return { message: MESSAGES.unsupported, retry: "pick" };
    case 422:
      return { message: MESSAGES.empty, retry: "pick" };
    case 507:
      return { message: MESSAGES.storageFull, retry: "resend" };
    default:
      return { message: MESSAGES.failed, retry: "resend" };
  }
}

/** Runs at most `limit` tasks at once; the rest wait in order. */
export function createLimiter(limit: number) {
  let active = 0;
  const waiting: (() => void)[] = [];
  const next = () => {
    if (active >= limit) return;
    const start = waiting.shift();
    if (start) {
      active += 1;
      start();
    }
  };
  return function run<T>(task: () => Promise<T>): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      waiting.push(() => {
        task()
          .then(resolve, reject)
          .finally(() => {
            active -= 1;
            next();
          });
      });
      next();
    });
  };
}
