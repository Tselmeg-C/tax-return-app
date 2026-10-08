/**
 * Tax items api client (#10): list, PATCH, manual item, persons and the German labels from
 * `GET /meta/labels` (the frontend keeps no copy of enum labels). Money is a dot-decimal
 * string on the wire.
 */
import { ApiError, apiFetch } from "@/lib/api";
import type { DocumentOut } from "@/lib/documents";

export const TAX_ITEMS_KEY = ["tax-items"] as const;
export const LABELS_KEY = ["meta-labels"] as const;
export const PERSONS_KEY = ["persons"] as const;
export const PAGE_SIZE = 100;

export type ItemFilter = "all" | "relevant" | "uncertain" | "attention" | "manual";

export interface TaxItemOut {
  id: string;
  version: number;
  document_id: string | null;
  year: number;
  category: string;
  anlage: string | null;
  zeile: string | null;
  gross_amount: string;
  deductible_amount: string;
  labour_share_35a: string | null;
  vendor: string | null;
  invoice_date: string | null;
  payment_date: string | null;
  payment_method: string;
  is_relevant: boolean;
  reason: string | null;
  confidence: string | null;
  overridden_by_user: boolean;
  person_id: string | null;
  created_at: string;
  updated_at: string;
  document: DocumentOut | null;
}

export interface Coded {
  code: string;
  label: string;
}

export interface Labels {
  categories: (Coded & { group: string; group_label: string })[];
  anlagen: Coded[];
  attention_reasons: Coded[];
  payment_methods: Coded[];
  doc_types: Coded[];
  supported_years: number[];
}

export interface Person {
  id: string;
  kind: "adult" | "child";
  first_name: string;
  last_name: string | null;
}

/** The editable fields (Decision 1); only changed ones are sent with `version`. */
export interface ItemFields {
  category: string;
  is_relevant: boolean;
  gross_amount: string;
  deductible_amount: string;
  labour_share_35a: string | null;
  year: number;
  person_id: string | null;
}

export const HOUSEHOLD_GROUP = "haushaltsnahe";
export const MANUAL_REASONS = [
  "classification_failed",
  "extraction_failed",
  "unreadable",
  "multiple_documents",
];

export const fetchLabels = () => apiFetch<Labels>("/meta/labels");
export const fetchPersons = () => apiFetch<Person[]>("/persons");

export function listTaxItems(params: {
  year: number;
  filter: ItemFilter;
  offset?: number;
  documentId?: string;
}): Promise<{ items: TaxItemOut[]; total: number }> {
  const query = new URLSearchParams();
  if (params.documentId) query.set("document_id", params.documentId);
  else if (params.filter !== "attention") query.set("year", String(params.year));
  if (params.filter !== "all") query.set("filter", params.filter);
  query.set("limit", String(PAGE_SIZE));
  if (params.offset) query.set("offset", String(params.offset));
  return apiFetch(`/tax-items?${query.toString()}`);
}

export function patchTaxItem(
  id: string,
  version: number,
  fields: Partial<ItemFields>,
): Promise<TaxItemOut> {
  return apiFetch(`/tax-items/${encodeURIComponent(id)}`, {
    method: "PATCH",
    json: { version, ...fields },
  });
}

export function createTaxItem(documentId: string, fields: ItemFields): Promise<TaxItemOut> {
  return apiFetch(`/documents/${encodeURIComponent(documentId)}/tax-items`, {
    method: "POST",
    json: fields,
  });
}

/** `?jahr=` (#10; #13 makes it global): a number, else unset (= the default year). */
export function belegeSearch(search: Record<string, unknown>): { jahr?: number } {
  const jahr = Number(search["jahr"]);
  return Number.isInteger(jahr) && jahr > 0 ? { jahr } : {};
}

/** Previous calendar year in Europe/Berlin if supported, else the highest supported year. */
export function defaultYear(supported: number[], now: Date = new Date()): number {
  const year = Number(
    new Intl.DateTimeFormat("de-DE", { timeZone: "Europe/Berlin", year: "numeric" }).format(now),
  );
  if (supported.includes(year - 1)) return year - 1;
  return supported.length ? Math.max(...supported) : year - 1;
}

export const label = (list: Coded[] | undefined, code: string | null | undefined) =>
  (code && list?.find((x) => x.code === code)?.label) || code || "";

export const isUncertain = (item: TaxItemOut) =>
  item.confidence !== null && Number(item.confidence) < 0.5 && !item.overridden_by_user;

export const TEXT = {
  saved: "Gespeichert",
  confirmed: "Als geprüft markiert",
  created: "Beleg erfasst",
  conflict:
    "Jemand anderes hat diesen Beleg gerade geändert. Die aktuellen Werte sind geladen – bitte prüfe sie und speichere erneut.",
  gone: "Dieser Beleg wurde inzwischen neu verarbeitet oder gelöscht. Die Liste wurde aktualisiert.",
  busy: "Wird gerade verarbeitet, bitte gleich nochmal versuchen",
  amount: "Bitte einen Betrag wie 12,50 eingeben",
  exceeds:
    "Der absetzbare Betrag darf nicht höher sein als der Bruttobetrag und muss dasselbe Vorzeichen haben",
  labour: "Der Lohnanteil muss zwischen 0 und dem Bruttobetrag liegen",
  categoryIrrelevant: "Bitte eine Kategorie wählen, um den Beleg als relevant zu markieren",
  householdOnly: "Haushaltsnahe Aufwendungen (§35a) gelten für den ganzen Haushalt",
  unknownPerson: "Diese Person gibt es im Haushalt nicht mehr. Bitte neu laden.",
  saveFailed: "Speichern fehlgeschlagen – bitte erneut versuchen",
  processing: "Wird gerade verarbeitet",
  manualLost: " Deine manuellen Änderungen gehen dabei verloren.",
  loading: "Belege werden geladen…",
  emptyFilter: "Keine Belege für diesen Filter.",
  loadError: "Belege konnten nicht geladen werden.",
  retry: "Erneut versuchen",
  processed: "Beleg verarbeitet",
} as const;

export const HINTS: Record<string, string> = {
  multiple_documents: "Bitte jede Rechnung einzeln hochladen – oder die Werte manuell erfassen.",
  unreadable:
    "Beleg nicht lesbar – bitte neu fotografieren und erneut hochladen, oder die Werte manuell erfassen.",
  classification_failed:
    "Automatische Erkennung fehlgeschlagen – bitte die Werte manuell erfassen oder den Beleg löschen und erneut hochladen.",
  extraction_failed:
    "Automatische Erkennung fehlgeschlagen – bitte die Werte manuell erfassen oder den Beleg löschen und erneut hochladen.",
  doc_type_not_supported:
    "Amtliche Dokumente werden bald unterstützt. Der Beleg bleibt gespeichert.",
};

export const emptyYear = (jahr: number) =>
  `Für ${jahr} gibt es noch keine Belege. Lade oben eine Rechnung hoch.`;

export const deleteConfirm = (name: string, overridden: boolean) =>
  `Beleg „${name}“ löschen? Datei und erkannte Werte werden entfernt.${overridden ? TEXT.manualLost : ""}`;

/** The German message for a failed save (`supported` fills the `unsupported_year` text). */
export function saveError(error: unknown, year: number, supported: number[]): string {
  if (!(error instanceof ApiError)) return TEXT.saveFailed;
  switch (error.detail) {
    case "document_busy":
      return TEXT.busy;
    case "invalid_amount":
      return TEXT.amount;
    case "deductible_exceeds_gross":
      return TEXT.exceeds;
    case "labour_share_invalid":
      return TEXT.labour;
    case "unsupported_year":
      return `Steuerjahr ${year} wird nicht unterstützt (nur ${supported.join(", ")})`;
    case "category_irrelevant":
      return TEXT.categoryIrrelevant;
    case "person_not_allowed":
      return TEXT.householdOnly;
    case "unknown_person":
      return TEXT.unknownPerson;
    default:
      return TEXT.saveFailed;
  }
}

/** `{label}` for a row: vendor, else the file name, else "Beleg vom <Datum>". */
export function documentName(doc: DocumentOut | null, created: string): string {
  return (
    doc?.original_filename ??
    `Beleg vom ${new Date(doc?.created_at ?? created).toLocaleDateString("de-DE")}`
  );
}
