import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import { DOCUMENTS_KEY, REPROCESS, reprocessDocument, reprocessMany } from "@/lib/documents";
import { TAX_ITEMS_KEY } from "@/lib/taxItems";

/** What the lists get: ask for a confirmation, then queue (`bulk` = owner-only bulk endpoint). */
export type AskReprocess = (ids: string[], bulk: boolean, onSuccess?: () => void) => void;

function problem(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 429) return REPROCESS.rateLimited;
    if (error.detail === "has_overrides") return REPROCESS.hasOverrides;
    if (error.detail === "document_busy") return REPROCESS.busy;
  }
  return REPROCESS.failed;
}

/** Confirmation dialog + request + result toast for "Neu verarbeiten" (#45). */
export function useReprocess(onDone?: () => void) {
  const queryClient = useQueryClient();
  const [asked, setAsked] = useState<{
    ids: string[];
    bulk: boolean;
    onSuccess?: () => void;
  } | null>(null);
  const [fresh, setFresh] = useState(false);
  const [sending, setSending] = useState(false);

  const ask: AskReprocess = (ids, bulk, onSuccess) => {
    setFresh(false);
    setAsked({ ids, bulk, ...(onSuccess ? { onSuccess } : {}) });
  };

  const run = async () => {
    if (!asked) return;
    setSending(true);
    try {
      const result = asked.bulk
        ? await reprocessMany(asked.ids, fresh)
        : await reprocessDocument(asked.ids[0]!, fresh);
      toast.success(REPROCESS.result(result.queued.length, result.skipped.length));
      asked.onSuccess?.();
      onDone?.();
    } catch (error) {
      toast.error(problem(error));
    } finally {
      setSending(false);
      setAsked(null);
      void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
      void queryClient.invalidateQueries({ queryKey: TAX_ITEMS_KEY });
    }
  };

  const dialog = asked ? (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-label={REPROCESS.button}
        className="w-full max-w-md rounded-lg border bg-card p-5 text-sm shadow-lg"
      >
        <p>{REPROCESS.confirm(asked.ids.length)}</p>
        <label className="mt-4 flex items-center gap-2">
          <input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} />
          {REPROCESS.fresh}
        </label>
        <div className="mt-5 flex justify-end gap-2">
          <button
            type="button"
            onClick={() => setAsked(null)}
            className="rounded border px-3 py-1 hover:bg-secondary"
          >
            {REPROCESS.cancel}
          </button>
          <button
            type="button"
            disabled={sending}
            onClick={() => void run()}
            className="rounded-md bg-primary px-3 py-1 text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
          >
            {REPROCESS.button}
          </button>
        </div>
      </div>
    </div>
  ) : null;

  return { ask, dialog };
}
