/**
 * Summary api client (#17): `GET /summary?year=`. Money is a dot-decimal string, a negative
 * `refund` is a Nachzahlung. Labels come from `GET /meta/labels` only.
 */
import { apiFetch } from "@/lib/api";
import type { Coded, Labels } from "@/lib/taxItems";

export const summaryKey = (jahr: number) => ["summary", jahr] as const;

export interface SummaryLabels extends Labels {
  deduction_notes: Coded[];
  estimate_missing: Coded[];
  pflicht_states: Coded[];
  pflicht_reasons: Coded[];
  pflicht_checks: Coded[];
  pflicht_deadline_kinds: Coded[];
}

export interface Pflicht {
  state: "mandatory" | "voluntary";
  reasons: string[];
  not_checked: string[];
  deadline_statutory: string;
  /** Shifted to the next working day (§ 108 AO): the one to show. */
  deadline: string;
  deadline_kind: string;
  overdue: boolean;
}

export interface Summary {
  year: number;
  status: "ok" | "no_profile";
  params_version: string | null;
  provisional: boolean;
  provisional_sections: string[];
  refund: string | null;
  zve: string | null;
  zve_before_children: string | null;
  gde: string | null;
  tarifliche_est: string | null;
  ermaessigung_35a: string | null;
  festzusetzende_est: string | null;
  soli: string | null;
  kirchensteuer: string | null;
  kindergeld_added: string | null;
  withheld: { lohnsteuer: string; soli: string; kirchensteuer: string } | null;
  by_anlage: { anlage: string; amount: string }[];
  pflicht: Pflicht | null;
  what_missing: { code: string; person_id: string | null }[];
  notes: { code: string; item_id: string | null; person_id: string | null }[];
  attention_count: number;
}

export const fetchSummary = (jahr: number) => apiFetch<Summary>(`/summary?year=${jahr}`);

export const isNachzahlung = (refund: string) => refund.startsWith("-");
/** `"-12.30"` → `"12.30"` */
export const absMoney = (value: string) => value.replace(/^-/, "");

export const formatDate = (iso: string) =>
  new Date(`${iso}T12:00:00`).toLocaleDateString("de-DE", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
  });

export const SUMMARY_TEXT = {
  loading: "Übersicht wird geladen…",
  loadError: "Die Übersicht konnte nicht geladen werden.",
  retry: "Erneut versuchen",
  unsupportedYear: (jahr: number) => `Steuerjahr nicht unterstützt (${jahr})`,
  noProfile: "Für dieses Steuerjahr fehlt noch das Steuerprofil.",
  toHaushalt: "Zum Haushalt",
  refund: "Voraussichtliche Erstattung",
  nachzahlung: "Voraussichtliche Nachzahlung",
  provisional: "vorläufig",
  mandatory: "Nach deinen Angaben ist die Abgabe Pflicht.",
  voluntary: "Nach deinen Angaben besteht keine Pflicht zur Abgabe (freiwillig).",
  notChecked: "Nicht geprüft",
  deadline: "Frist",
  overdue: "Frist abgelaufen",
  nothingMissing: "Nach deinen Angaben fehlt nichts.",
  attention: (n: number) =>
    n === 1 ? "1 Beleg braucht deine Aufmerksamkeit" : `${n} Belege brauchen deine Aufmerksamkeit`,
} as const;
