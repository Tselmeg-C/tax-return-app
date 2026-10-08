import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ExternalLink, Globe, Pencil, Send, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { ItemForm } from "@/components/documents/ItemForm";
import { ApiError } from "@/lib/api";
import { deleteDocument, DOCUMENTS_KEY, isRunning, MESSAGES } from "@/lib/documents";
import { formatEur } from "@/lib/money";
import {
  deleteConfirm,
  documentName,
  emptyYear,
  isUncertain,
  label,
  listTaxItems,
  patchTaxItem,
  saveError,
  TAX_ITEMS_KEY,
  TEXT,
  type ItemFilter,
  type Labels,
  type Person,
  type TaxItemOut,
} from "@/lib/taxItems";

const CHIPS: [ItemFilter, string][] = [
  ["all", "Alle"],
  ["relevant", "Relevant"],
  ["uncertain", "Unsicher"],
  ["attention", "Prüfen"],
  ["manual", "Manuell"],
];

const COLS = "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,2fr)_7rem_7rem_auto]";
const COLS_YEAR = "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,2fr)_3.5rem_7rem_7rem_auto]";

/** "Belege {jahr}": filter chips and the household's tax items (#10). */
export function TaxItemList(props: { jahr: number; labels: Labels; persons: Person[] }) {
  const { jahr, labels, persons } = props;
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<ItemFilter>("all");
  const [editing, setEditing] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const query = useInfiniteQuery({
    queryKey: [...TAX_ITEMS_KEY, jahr, filter],
    queryFn: ({ pageParam }) => listTaxItems({ year: jahr, filter, offset: pageParam }),
    initialPageParam: 0,
    getNextPageParam: (last, pages) => {
      const shown = pages.reduce((n, p) => n + p.items.length, 0);
      return shown < last.total ? shown : undefined;
    },
  });
  const items = query.data?.pages.flatMap((p) => p.items) ?? [];
  const withYear = filter === "attention";
  const cols = withYear ? COLS_YEAR : COLS;

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: TAX_ITEMS_KEY });
    void queryClient.invalidateQueries({ queryKey: DOCUMENTS_KEY });
  };
  const gone = () => {
    setEditing(null);
    setNotice(TEXT.gone);
    refresh();
  };
  const saved = (text: string) => {
    setEditing(null);
    setNotice(null);
    toast.success(text);
    refresh();
  };

  const confirmItem = async (item: TaxItemOut) => {
    try {
      await patchTaxItem(item.id, item.version, {});
      saved(TEXT.confirmed);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return gone();
      setNotice(
        e instanceof ApiError && e.detail === "version_conflict"
          ? TEXT.conflict
          : saveError(e, item.year, labels.supported_years),
      );
      refresh();
    }
  };

  const remove = async (item: TaxItemOut) => {
    if (!item.document_id) return;
    const name = item.vendor ?? documentName(item.document, item.created_at);
    if (!window.confirm(deleteConfirm(name, item.overridden_by_user))) return;
    try {
      await deleteDocument(item.document_id);
      setNotice(null);
      refresh();
    } catch (e) {
      setNotice(e instanceof ApiError && e.status === 409 ? MESSAGES.busy : MESSAGES.failed);
    }
  };

  const personName = (id: string | null) => {
    const p = persons.find((x) => x.id === id);
    return p ? [p.first_name, p.last_name].filter(Boolean).join(" ") : "Haushalt";
  };

  return (
    <section className="mt-10" aria-label={`Belege ${jahr}`}>
      <h2 className="text-2xl">Belege {jahr}</h2>
      <div className="mt-3 flex flex-wrap gap-2">
        {CHIPS.map(([code, text]) => (
          <button
            key={code}
            type="button"
            aria-pressed={filter === code}
            onClick={() => {
              setFilter(code);
              setEditing(null);
            }}
            className={`rounded-full border px-3 py-1 text-sm ${filter === code ? "bg-ink text-paper" : "bg-card hover:bg-secondary"}`}
          >
            {text}
          </button>
        ))}
      </div>
      {notice && (
        <p role="status" className="mt-3 text-sm text-destructive">
          {notice}
        </p>
      )}

      <div className="sheet mt-4 text-sm">
        <div className={`stamp hidden gap-3 border-b p-3 text-muted-foreground sm:grid ${cols}`}>
          <span>Beleg</span>
          <span>Person</span>
          <span>Zuordnung</span>
          {withYear && <span>Jahr</span>}
          <span className="text-right">Brutto</span>
          <span className="text-right">Absetzbar</span>
          <span />
        </div>

        {query.isPending && (
          <div aria-busy="true">
            <p className="p-3 text-muted-foreground">{TEXT.loading}</p>
            {[1, 2, 3].map((n) => (
              <div key={n} className="m-3 h-8 animate-pulse rounded bg-secondary" />
            ))}
          </div>
        )}
        {query.isError && (
          <div className="flex items-center gap-3 p-3">
            <span className="text-destructive">{TEXT.loadError}</span>
            <button
              type="button"
              onClick={() => void query.refetch()}
              className="rounded border px-2 py-1 hover:bg-secondary"
            >
              {TEXT.retry}
            </button>
          </div>
        )}
        {query.isSuccess && items.length === 0 && (
          <p className="p-3 text-muted-foreground">
            {filter === "all" ? emptyYear(jahr) : TEXT.emptyFilter}
          </p>
        )}

        <ul className="divide-y divide-dashed">
          {items.map((item) => {
            const doc = item.document;
            const busy = doc !== null && isRunning(doc.status);
            const attention = doc?.status === "needs_attention";
            const file = documentName(doc, item.created_at);
            const anlage = label(labels.anlagen, item.anlage);
            return (
              <li
                key={item.id}
                data-testid="tax-item-row"
                className={`grid grid-cols-[minmax(0,1fr)] gap-2 p-3 sm:items-center sm:gap-3 ${cols} ${item.is_relevant ? "" : "text-muted-foreground"}`}
              >
                <div className="flex min-w-0 items-center gap-2">
                  {doc?.channel === "telegram" ? (
                    <Send className="h-3.5 w-3.5 shrink-0 text-primary" />
                  ) : (
                    <Globe className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                  )}
                  <div className="min-w-0">
                    {/* Rendered as text, never as HTML. */}
                    <p className="truncate font-medium">{item.vendor ?? file}</p>
                    {item.vendor && (
                      <p className="truncate text-xs text-muted-foreground">{file}</p>
                    )}
                  </div>
                </div>
                <div>{personName(item.person_id)}</div>
                <div>
                  {item.is_relevant ? (
                    <>
                      <p>{label(labels.categories, item.category)}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.anlage === null
                          ? `Keine Zuordnung für ${item.year}`
                          : item.zeile
                            ? `${anlage} · Zeile ${item.zeile}`
                            : anlage}
                      </p>
                    </>
                  ) : (
                    <span className="stamp text-muted-foreground">nicht steuerrelevant</span>
                  )}
                  <div className="mt-1 flex flex-wrap gap-1">
                    {item.overridden_by_user && <Badge>manuell</Badge>}
                    {isUncertain(item) && <Badge>unsicher</Badge>}
                    {attention && (
                      <Badge>{label(labels.attention_reasons, doc?.attention_reason)}</Badge>
                    )}
                  </div>
                </div>
                {withYear && <div className="num">{item.year}</div>}
                <div className="num sm:text-right">
                  <span className="text-xs text-muted-foreground sm:hidden">Brutto </span>
                  {formatEur(item.gross_amount)}
                </div>
                <div className="num sm:text-right">
                  <span className="text-xs text-muted-foreground sm:hidden">Absetzbar </span>
                  {formatEur(item.deductible_amount)}
                  {item.labour_share_35a !== null && (
                    <p className="text-xs text-muted-foreground">
                      Lohnanteil §35a {formatEur(item.labour_share_35a)}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-1">
                  <IconButton
                    onClick={() => setEditing(editing === item.id ? null : item.id)}
                    icon={<Pencil className="h-3.5 w-3.5" />}
                    text="Bearbeiten"
                  />
                  {attention && (
                    <IconButton
                      onClick={() => void confirmItem(item)}
                      icon={<Check className="h-3.5 w-3.5" />}
                      text="Passt so"
                    />
                  )}
                  {doc && (
                    <a
                      href={`/api/documents/${doc.id}/file`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="flex items-center gap-1 rounded px-2 py-1 hover:bg-secondary"
                    >
                      <ExternalLink className="h-3.5 w-3.5" /> Original öffnen
                    </a>
                  )}
                  {doc && (
                    <IconButton
                      onClick={() => void remove(item)}
                      icon={<Trash2 className="h-3.5 w-3.5" />}
                      text="Löschen"
                    />
                  )}
                </div>
                {editing === item.id && (
                  <div className="min-w-0 sm:col-span-full">
                    <ItemForm
                      item={item}
                      documentId={item.document_id ?? ""}
                      year={jahr}
                      busy={busy}
                      labels={labels}
                      persons={persons}
                      onSaved={() => saved(TEXT.saved)}
                      onGone={gone}
                      onCancel={() => setEditing(null)}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ul>
        {query.hasNextPage && (
          <button
            type="button"
            onClick={() => void query.fetchNextPage()}
            className="m-3 rounded border px-3 py-1 hover:bg-secondary"
          >
            Mehr laden
          </button>
        )}
      </div>
    </section>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="stamp inline-block rounded-sm bg-accent px-1 text-accent-foreground">
      {children}
    </span>
  );
}

function IconButton(props: { onClick: () => void; icon: React.ReactNode; text: string }) {
  return (
    <button
      type="button"
      onClick={props.onClick}
      className="flex items-center gap-1 rounded px-2 py-1 hover:bg-secondary"
    >
      {props.icon} {props.text}
    </button>
  );
}
