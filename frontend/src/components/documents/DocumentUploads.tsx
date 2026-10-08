import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Camera,
  ExternalLink,
  Globe,
  PenLine,
  RotateCcw,
  Send,
  Trash2,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ItemForm } from "@/components/documents/ItemForm";
import { ApiError } from "@/lib/api";
import {
  createLimiter,
  deleteDocument,
  DOCUMENTS_KEY,
  isRunning,
  listDocuments,
  MAX_PARALLEL_UPLOADS,
  MESSAGES,
  POLL_MS,
  precheck,
  uploadDocument,
  uploadProblem,
  type DocumentOut,
  type UploadProblem,
} from "@/lib/documents";
import {
  deleteConfirm,
  HINTS,
  label,
  listTaxItems,
  MANUAL_REASONS,
  TAX_ITEMS_KEY,
  TEXT,
  type Labels,
  type Person,
} from "@/lib/taxItems";

/** What the Belege page adds (#10): reason labels, manual items, the "verarbeitet" toast. */
export interface BelegeContext {
  jahr: number;
  labels: Labels;
  persons: Person[];
  onShowYear: (year: number) => void;
}

interface PendingUpload {
  key: number;
  file: File;
  problem: UploadProblem | null; // null = "wird hochgeladen…"
}

const when = (iso: string) =>
  new Date(iso).toLocaleString("de-DE", { dateStyle: "short", timeStyle: "short" });

/**
 * Upload area (drop zone, file picker, camera) and the "Hochgeladene Belege" list: documents
 * without a tax item (in flight, failed, needs attention without item).
 */
export function DocumentUploads({ belege }: { belege?: BelegeContext }) {
  const queryClient = useQueryClient();
  const documents = useQuery({
    queryKey: DOCUMENTS_KEY,
    queryFn: listDocuments,
    refetchInterval: (query) =>
      (query.state.data ?? []).some((d) => isRunning(d.status)) ? POLL_MS : false,
  });
  const [pending, setPending] = useState<PendingUpload[]>([]);
  const [notices, setNotices] = useState<Record<string, string>>({});
  const [drag, setDrag] = useState(false);
  const pickRef = useRef<HTMLInputElement>(null);
  const held = useRef(new Map<string, File>()); // document id -> File, for "Erneut hochladen"
  const limit = useRef(createLimiter(MAX_PARALLEL_UPLOADS));
  const nextKey = useRef(0);
  const [manual, setManual] = useState<string | null>(null); // document id with an open form
  const running = useRef(new Set<string>());
  const removed = useRef(new Set<string>());

  // A row that was queued / processing left the list: it has an item now (#10).
  useEffect(() => {
    const data = documents.data;
    if (!data) return;
    const ids = new Set(data.map((d) => d.id));
    const left = [...running.current].filter((id) => !ids.has(id) && !removed.current.has(id));
    running.current = new Set(data.filter((d) => isRunning(d.status)).map((d) => d.id));
    if (!belege) return;
    for (const id of left) {
      void listTaxItems({ year: belege.jahr, filter: "all", documentId: id }).then(
        ({ items }) => {
          const year = items[0]?.year;
          if (year === undefined) return;
          void queryClient.invalidateQueries({ queryKey: TAX_ITEMS_KEY });
          if (year === belege.jahr) toast.success(TEXT.processed);
          else
            toast.success(`${TEXT.processed} – Steuerjahr ${year}`, {
              action: { label: "Anzeigen", onClick: () => belege.onShowYear(year) },
            });
        },
        () => undefined,
      );
    }
  }, [documents.data, belege, queryClient]);

  const notice = (id: string, text: string | null) =>
    setNotices((n) => {
      const copy = { ...n };
      if (text === null) delete copy[id];
      else copy[id] = text;
      return copy;
    });

  const putDocument = (doc: DocumentOut) => {
    queryClient.setQueryData<DocumentOut[]>(DOCUMENTS_KEY, (old = []) => [
      doc,
      ...old.filter((d) => d.id !== doc.id),
    ]);
  };

  /** Upload `file`; returns the problem, or null on success (incl. duplicates). */
  const upload = async (file: File): Promise<UploadProblem | null> => {
    try {
      const result = await limit.current(() => uploadDocument(file));
      held.current.set(result.document.id, file);
      putDocument(result.document);
      notice(
        result.document.id,
        result.duplicate ? (result.requeued ? MESSAGES.requeued : MESSAGES.duplicate) : null,
      );
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      return null;
    } catch (error) {
      return uploadProblem(error);
    }
  };

  const send = async (key: number, file: File) => {
    setPending((p) => p.map((u) => (u.key === key ? { ...u, problem: null } : u)));
    const problem = await upload(file);
    setPending((p) =>
      problem === null
        ? p.filter((u) => u.key !== key)
        : p.map((u) => (u.key === key ? { ...u, problem } : u)),
    );
  };

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const added = Array.from(files).map((file) => ({
      key: (nextKey.current += 1),
      file,
      problem: precheck(file),
    }));
    setPending((p) => [...added, ...p]);
    for (const item of added) if (item.problem === null) void send(item.key, item.file);
  };

  const retryPending = (item: PendingUpload) => {
    if (item.problem?.retry === "resend") void send(item.key, item.file);
    else pickRef.current?.click();
  };

  const retryDocument = async (doc: DocumentOut) => {
    const file = held.current.get(doc.id);
    if (!file) {
      pickRef.current?.click();
      return;
    }
    const problem = await upload(file);
    if (problem) notice(doc.id, problem.message);
  };

  const remove = async (doc: DocumentOut) => {
    if (!window.confirm(deleteConfirm(nameOf(doc), false))) return;
    try {
      await deleteDocument(doc.id);
      removed.current.add(doc.id);
      queryClient.setQueryData<DocumentOut[]>(DOCUMENTS_KEY, (old = []) =>
        old.filter((d) => d.id !== doc.id),
      );
      held.current.delete(doc.id);
    } catch (error) {
      notice(
        doc.id,
        error instanceof ApiError && error.status === 409 ? MESSAGES.busy : MESSAGES.failed,
      );
    }
  };

  const onInput = (event: React.ChangeEvent<HTMLInputElement>) => {
    addFiles(event.target.files);
    event.target.value = ""; // the same file can be picked again
  };

  return (
    <>
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDrag(true);
        }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDrag(false);
          addFiles(e.dataTransfer.files);
        }}
        data-testid="dropzone"
        className={`mt-6 flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-10 text-center transition-colors ${drag ? "border-primary bg-primary/5" : "bg-card"}`}
      >
        <Upload className="h-8 w-8 text-primary" />
        <p className="font-display text-xl">Fotos oder PDFs hier ablegen</p>
        <p className="text-sm text-muted-foreground">
          Oder einfach an den Telegram-Bot schicken — er sortiert automatisch.
        </p>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => pickRef.current?.click()}
            className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
          >
            <Upload className="h-4 w-4" /> Dateien wählen
          </button>
          <label className="flex cursor-pointer items-center gap-2 rounded-md border bg-background px-4 py-2 text-sm hover:bg-secondary">
            <Camera className="h-4 w-4" /> Foto
            <input
              type="file"
              accept="image/*"
              capture="environment"
              className="hidden"
              data-testid="camera-input"
              onChange={onInput}
            />
          </label>
        </div>
        <input
          ref={pickRef}
          type="file"
          multiple
          accept="image/*,application/pdf"
          className="hidden"
          data-testid="file-input"
          onChange={onInput}
        />
      </div>

      <section className="mt-8" aria-label="Hochgeladene Belege">
        <h2 className="text-2xl">Hochgeladene Belege</h2>
        <ul className="sheet mt-3 divide-y divide-dashed text-sm">
          {pending.map((item) => (
            <Row
              key={`pending-${item.key}`}
              label={item.file.name}
              secondary={null}
              status={
                item.problem === null ? (
                  <Running text="wird hochgeladen…" />
                ) : (
                  <span className="text-destructive">{item.problem.message}</span>
                )
              }
              actions={item.problem !== null && <RetryButton onClick={() => retryPending(item)} />}
            />
          ))}
          {(documents.data ?? []).map((doc) => {
            const reason = doc.status === "needs_attention" ? doc.attention_reason : null;
            const canManual = Boolean(belege && reason && MANUAL_REASONS.includes(reason));
            return (
              <Row
                key={doc.id}
                channel={doc.channel}
                label={nameOf(doc)}
                secondary={doc.original_filename ? when(doc.created_at) : null}
                below={
                  (reason && HINTS[reason]) || manual === doc.id ? (
                    <>
                      {reason && HINTS[reason] && (
                        <p className="text-xs text-muted-foreground">{HINTS[reason]}</p>
                      )}
                      {belege && manual === doc.id && (
                        <div className="mt-2">
                          <ItemForm
                            item={null}
                            documentId={doc.id}
                            year={belege.jahr}
                            busy={false}
                            labels={belege.labels}
                            persons={belege.persons}
                            onSaved={() => {
                              setManual(null);
                              toast.success(TEXT.created);
                              void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
                              void queryClient.invalidateQueries({ queryKey: TAX_ITEMS_KEY });
                            }}
                            onGone={() => {
                              setManual(null);
                              void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
                            }}
                            onCancel={() => setManual(null)}
                          />
                        </div>
                      )}
                    </>
                  ) : null
                }
                status={
                  <>
                    <DocStatus doc={doc} labels={belege?.labels} />
                    {notices[doc.id] && (
                      <span className="ml-2 text-muted-foreground">{notices[doc.id]}</span>
                    )}
                  </>
                }
                actions={
                  <>
                    {doc.status === "failed" && (
                      <RetryButton onClick={() => void retryDocument(doc)} />
                    )}
                    {canManual && (
                      <button
                        type="button"
                        onClick={() => setManual(manual === doc.id ? null : doc.id)}
                        className="flex items-center gap-1 rounded border px-2 py-1 hover:bg-secondary"
                      >
                        <PenLine className="h-3.5 w-3.5" /> Manuell erfassen
                      </button>
                    )}
                    <a
                      href={`/api/documents/${doc.id}/file`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 rounded px-2 py-1 hover:bg-secondary"
                    >
                      <ExternalLink className="h-3.5 w-3.5" /> Original öffnen
                    </a>
                    <button
                      type="button"
                      onClick={() => void remove(doc)}
                      className="flex items-center gap-1 rounded px-2 py-1 hover:bg-secondary"
                    >
                      <Trash2 className="h-3.5 w-3.5" /> Löschen
                    </button>
                  </>
                }
              />
            );
          })}
          {pending.length === 0 && documents.data?.length === 0 && (
            <li className="p-3 text-muted-foreground">Noch keine Belege hochgeladen.</li>
          )}
        </ul>
      </section>
    </>
  );
}

const nameOf = (doc: DocumentOut) => doc.original_filename ?? `Beleg vom ${when(doc.created_at)}`;

function Row(props: {
  label: string;
  secondary: string | null;
  status: ReactNode;
  actions: ReactNode;
  channel?: "web" | "telegram";
  below?: ReactNode;
}) {
  return (
    <li className="flex flex-wrap items-center gap-3 p-3" data-testid="document-row">
      {props.channel === "telegram" ? (
        <Send className="h-3.5 w-3.5 text-primary" />
      ) : (
        <Globe className="h-3.5 w-3.5 text-muted-foreground" />
      )}
      <div className="min-w-0 flex-1">
        {/* Rendered as text, never as HTML. */}
        <p className="truncate font-medium">{props.label}</p>
        {props.secondary && <p className="num text-xs text-muted-foreground">{props.secondary}</p>}
      </div>
      <div className="text-sm">{props.status}</div>
      <div className="flex flex-wrap items-center gap-1">{props.actions}</div>
      {props.below && <div className="w-full">{props.below}</div>}
    </li>
  );
}

function Running({ text }: { text: string }) {
  return <span className="stamp animate-pulse text-muted-foreground">{text}</span>;
}

function DocStatus({ doc, labels }: { doc: DocumentOut; labels: Labels | undefined }) {
  if (isRunning(doc.status)) return <Running text="läuft…" />;
  if (doc.status === "done") return <span className="stamp text-success">verarbeitet</span>;
  if (doc.status === "failed")
    return <span className="text-destructive">{MESSAGES.processingFailed}</span>;
  return (
    <span className="stamp">
      {labels && doc.attention_reason
        ? label(labels.attention_reasons, doc.attention_reason)
        : "Prüfung nötig"}
    </span>
  );
}

function RetryButton({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="flex items-center gap-1 rounded border px-2 py-1 hover:bg-secondary"
    >
      <RotateCcw className="h-3.5 w-3.5" /> Erneut hochladen
    </button>
  );
}
