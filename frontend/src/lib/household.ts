/**
 * Household api client (#13): `GET /household/{jahr}`, persons, profile, employments, child
 * rows and copy, plus the German texts. Labels come from `GET /meta/labels` only.
 *
 * Writes are plain `apiFetch` calls (no `useMutation`): a typed Steuer-ID must never land in
 * the query or mutation cache. Responses carry only `steuer_id_masked`.
 */
import { ApiError, apiFetch } from "@/lib/api";
import type { Coded, Labels } from "@/lib/taxItems";

export const householdKey = (jahr: number) => ["household", jahr] as const;

export interface HouseholdLabels extends Labels {
  bundeslaender: Coded[];
  filing_statuses: Coded[];
  steuerklassen: Coded[];
  religions: Coded[];
  allowance_shares: Coded[];
  person_kinds: Coded[];
}

export interface PersonOut {
  id: string;
  kind: "adult" | "child";
  first_name: string;
  last_name: string | null;
  dob: string | null;
  religion: string;
  disability_grade: number | null;
  merkzeichen_h_bl_tbl: boolean;
  steuer_id_masked: string | null;
  is_me: boolean;
}

export interface ProfileOut {
  year: number;
  filing_status: "single" | "joint";
  bundesland: string;
  taxpayer_person_id: string;
  spouse_person_id: string | null;
}

export interface EmploymentOut {
  id: string;
  person_id: string;
  year: number;
  employer_name: string;
  steuerklasse: string;
  has_factor: boolean;
  commute_km: number | null;
  office_days: number;
  homeoffice_days: number;
  in_return: boolean;
}

export interface ChildYearOut {
  person_id: string;
  year: number;
  months: number;
  allowance_share: "full" | "half";
  in_household: boolean;
}

export interface ChildEntry {
  person_id: string;
  max_months: number;
  child_year: ChildYearOut | null;
}

export interface HouseholdOut {
  year: number;
  profile: ProfileOut | null;
  persons: PersonOut[];
  employments: EmploymentOut[];
  children: ChildEntry[];
  years_with_profile: number[];
}

/** Person fields as sent; `steuer_id` only when the user typed one (or `null` = remove). */
export interface PersonFields {
  first_name: string;
  last_name: string | null;
  dob: string | null;
  religion: string;
  disability_grade: number | null;
  merkzeichen_h_bl_tbl: boolean;
  steuer_id?: string | null;
}

export interface EmploymentFields {
  employer_name: string;
  steuerklasse: string;
  has_factor: boolean;
  commute_km: number | null;
  office_days: number;
  homeoffice_days: number;
}

const enc = encodeURIComponent;

export const fetchHousehold = (jahr: number) => apiFetch<HouseholdOut>(`/household/${jahr}`);

export const createPerson = (
  fields: PersonFields & { kind: "adult" | "child"; link_to_me?: boolean },
) => apiFetch<PersonOut>("/persons", { method: "POST", json: fields });

export const patchPerson = (id: string, fields: Partial<PersonFields>) =>
  apiFetch<PersonOut>(`/persons/${enc(id)}`, { method: "PATCH", json: fields });

export const deletePerson = (id: string) =>
  apiFetch<void>(`/persons/${enc(id)}`, { method: "DELETE" });

export const putProfile = (jahr: number, body: Omit<ProfileOut, "year">): Promise<ProfileOut> =>
  apiFetch(`/household/${jahr}/profile`, { method: "PUT", json: body });

export const createEmployment = (person_id: string, year: number, fields: EmploymentFields) =>
  apiFetch<EmploymentOut>("/employments", {
    method: "POST",
    json: { person_id, year, ...fields },
  });

export const patchEmployment = (id: string, fields: Partial<EmploymentFields>) =>
  apiFetch<EmploymentOut>(`/employments/${enc(id)}`, { method: "PATCH", json: fields });

export const deleteEmployment = (id: string) =>
  apiFetch<void>(`/employments/${enc(id)}`, { method: "DELETE" });

export const putChildYear = (
  jahr: number,
  personId: string,
  body: Omit<ChildYearOut, "person_id" | "year">,
) =>
  apiFetch<ChildYearOut>(`/household/${jahr}/children/${enc(personId)}`, {
    method: "PUT",
    json: body,
  });

export const copyYear = (jahr: number, from_year: number) =>
  apiFetch<HouseholdOut>(`/household/${jahr}/copy`, { method: "POST", json: { from_year } });

export const fullName = (p: Pick<PersonOut, "first_name" | "last_name">) =>
  [p.first_name, p.last_name].filter(Boolean).join(" ");

/** Age on 31.12. of `jahr`. */
export const ageAtYearEnd = (dob: string, jahr: number) => jahr - Number(dob.slice(0, 4));

/** The 18+ hint applies when the child turns 18 in the year or is 18–24 during it. */
export const adultChildHint = (dob: string | null, jahr: number) =>
  dob !== null && ageAtYearEnd(dob, jahr) >= 18 && ageAtYearEnd(dob, jahr) <= 25;

export const isChurchMember = (p: PersonOut) => p.religion !== "none";

export const STEUER_ID_RE = /^\d{11}$/;
export const DISABILITY_GRADES = [20, 30, 40, 50, 60, 70, 80, 90, 100];

export const TEXT = {
  saved: "Gespeichert",
  invalidSteuerId:
    "Die Steuer-ID ist ungültig. Bitte prüfe die 11 Ziffern (Steuerbescheid oder Lohnsteuerbescheinigung).",
  steuerIdDigits: "Die Steuer-ID hat 11 Ziffern.",
  duplicateSteuerId: "Diese Steuer-ID ist schon bei einer anderen Person eingetragen.",
  invalidDob: "Bitte ein gültiges Geburtsdatum eingeben (nicht in der Zukunft).",
  spouseRequired: "Für die Zusammenveranlagung bitte die Partnerin oder den Partner angeben.",
  factorRequiresIv: "Das Faktorverfahren gibt es nur mit Steuerklasse IV.",
  adultChild:
    "Ab 18 zählen nur Monate mit Ausbildung, Studium, Freiwilligendienst oder Arbeitssuche.",
  childrenReview: "Die Veranlagung hat sich geändert – bitte prüfe die Kinderfreibeträge.",
  churchHint:
    "Nur eine Person ist Kirchenmitglied – die Kirchensteuer wird in der Schätzung noch vereinfacht berechnet.",
  saveFailed: "Speichern fehlgeschlagen – bitte erneut versuchen",
  loading: "Haushalt wird geladen…",
  loadError: "Haushalt konnte nicht geladen werden.",
  retry: "Erneut versuchen",
  invalidName: "Bitte einen Vornamen eingeben (höchstens 100 Zeichen).",
} as const;

export const copied = (from: number) => `Haushalt aus ${from} übernommen – bitte prüfen`;
export const notInReturn = (jahr: number) => `Nicht Teil der Erklärung ${jahr}`;
export const unsupportedYear = (jahr: number, supported: number[]) =>
  `Steuerjahr ${jahr} wird nicht unterstützt (nur ${supported.join(", ")})`;
export const deleteConfirm = (name: string) =>
  `${name} löschen? Arbeitsverhältnisse und Kinderangaben dieser Person werden für alle Jahre entfernt. Belege bleiben erhalten.`;

/** Field-level message for a failed save (`field` = where to show it). */
export interface SaveError {
  field: string | null;
  message: string;
}

export function saveError(
  error: unknown,
  ctx: { jahr: number; name?: string; maxMonths?: number },
): SaveError {
  if (!(error instanceof ApiError) || error.status >= 500) {
    return { field: null, message: TEXT.saveFailed };
  }
  const body = (error.body ?? {}) as { field?: string; count?: number; years?: number[] };
  const field = body.field ?? null;
  const name = ctx.name ?? "Diese Person";
  const days = String(new Date(ctx.jahr, 1, 29).getMonth() === 1 ? 366 : 365);
  const messages: Record<string, string> = {
    invalid_steuer_id: TEXT.invalidSteuerId,
    duplicate_steuer_id: TEXT.duplicateSteuerId,
    invalid_dob: TEXT.invalidDob,
    invalid_name: TEXT.invalidName,
    spouse_required: TEXT.spouseRequired,
    person_not_in_return: `Diese Person ist nicht Teil der Erklärung ${ctx.jahr}.`,
    days_exceed_year: `Büro- und Homeoffice-Tage zusammen dürfen ${days} nicht übersteigen.`,
    factor_requires_iv: TEXT.factorRequiresIv,
    months_exceed: `Für ${ctx.jahr} sind höchstens ${ctx.maxMonths ?? 12} Monate möglich.`,
    child_too_old: `${name} ist ${ctx.jahr} schon über 25 – kein Kindergeld und kein Kinderfreibetrag mehr.`,
    child_not_born: `${name} ist erst nach ${ctx.jahr} geboren.`,
    person_has_tax_items: `${name} ist noch ${body.count ?? ""} Belegen zugeordnet. Ordne diese Belege zuerst einer anderen Person oder dem Haushalt zu (Seite Belege).`,
    person_in_profile: `${name} ist in der Veranlagung ${(body.years ?? []).join(", ")} eingetragen. Ändere zuerst die Veranlagung.`,
    year_not_empty: `Für ${ctx.jahr} gibt es schon Angaben.`,
  };
  return { field, message: (error.detail && messages[error.detail]) || TEXT.saveFailed };
}
