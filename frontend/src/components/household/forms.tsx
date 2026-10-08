import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { ApiError } from "@/lib/api";
import {
  createEmployment,
  createPerson,
  DISABILITY_GRADES,
  putProfile,
  patchEmployment,
  patchPerson,
  putChildYear,
  saveError,
  STEUER_ID_RE,
  TEXT,
  type ChildEntry,
  type EmploymentOut,
  type HouseholdLabels,
  type HouseholdOut,
  type ProfileOut,
  type PersonFields,
  type PersonOut,
  type SaveError,
} from "@/lib/household";

export const inputClass = "min-h-11 w-full rounded-md border bg-card px-3 py-2 text-sm";
export const buttonClass =
  "min-h-11 w-full rounded-md border px-4 py-2 text-sm hover:bg-secondary disabled:opacity-50 sm:w-auto";
export const primaryClass =
  "min-h-11 w-full rounded-md bg-ink px-4 py-2 text-sm text-paper hover:opacity-90 disabled:opacity-50 sm:w-auto";

export function Field(props: {
  label: string;
  error?: string | null;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="block text-sm">
      <span className="text-muted-foreground">{props.label}</span>
      <div className="mt-1">{props.children}</div>
      {props.hint ? (
        <span className="mt-1 block text-xs text-muted-foreground">{props.hint}</span>
      ) : null}
      {props.error ? (
        <span role="alert" className="mt-1 block text-xs text-destructive">
          {props.error}
        </span>
      ) : null}
    </label>
  );
}

export function FormError({ error }: { error: SaveError | null }) {
  return error && !error.field ? (
    <p role="alert" className="text-sm text-destructive">
      {error.message}
    </p>
  ) : null;
}

const errorAt = (error: SaveError | null, field: string) =>
  error?.field === field ? error.message : null;

// --- persons -------------------------------------------------------------------------------

interface PersonDraft {
  first_name: string;
  last_name: string;
  dob: string;
  religion: string;
  disability: string; // "" = keiner
  steuerId: string; // typed value only; cleared after every successful save
  steuerIdMode: "keep" | "edit" | "remove";
}

const draftOf = (p: PersonOut | undefined): PersonDraft => ({
  first_name: p?.first_name ?? "",
  last_name: p?.last_name ?? "",
  dob: p?.dob ?? "",
  religion: p?.religion ?? "none",
  disability: p?.disability_grade ? String(p.disability_grade) : "",
  steuerId: "",
  steuerIdMode: p?.steuer_id_masked ? "keep" : "edit",
});

/** The person inputs (state is owned by the caller, so the partner form can live inside the
 * profile form). */
export function usePersonDraft(person?: PersonOut) {
  const [draft, setDraft] = useState(() => draftOf(person));
  const set = (patch: Partial<PersonDraft>) => setDraft((d) => ({ ...d, ...patch }));
  /** The fields to send, or the client-side error (Steuer-ID not 11 digits). */
  const fields = (): PersonFields | SaveError => {
    const typed = draft.steuerId.replace(/\s/g, "");
    if (draft.steuerIdMode === "edit" && typed && !STEUER_ID_RE.test(typed)) {
      return { field: "steuer_id", message: TEXT.steuerIdDigits };
    }
    const out: PersonFields = {
      first_name: draft.first_name,
      last_name: draft.last_name.trim() || null,
      dob: draft.dob || null,
      religion: draft.religion,
      disability_grade: draft.disability ? Number(draft.disability) : null,
    };
    if (draft.steuerIdMode === "edit" && typed) out.steuer_id = typed;
    if (draft.steuerIdMode === "remove") out.steuer_id = null;
    return out;
  };
  const saved = (p: PersonOut) => setDraft(draftOf(p));
  return { draft, set, fields, saved };
}

export type PersonDraftApi = ReturnType<typeof usePersonDraft>;

export function PersonInputs(props: {
  api: PersonDraftApi;
  kind: "adult" | "child";
  masked: string | null;
  labels: HouseholdLabels;
  error: SaveError | null;
}) {
  const { draft, set } = props.api;
  const err = (field: string) => errorAt(props.error, field);
  return (
    <div className="grid gap-4 sm:grid-cols-2">
      <Field label="Vorname" error={err("first_name")}>
        <input
          className={inputClass}
          value={draft.first_name}
          onChange={(e) => set({ first_name: e.target.value })}
        />
      </Field>
      <Field label="Nachname" error={err("last_name")}>
        <input
          className={inputClass}
          value={draft.last_name}
          onChange={(e) => set({ last_name: e.target.value })}
        />
      </Field>
      <Field label="Geburtsdatum" error={err("dob")}>
        <input
          type="date"
          className={inputClass}
          value={draft.dob}
          onChange={(e) => set({ dob: e.target.value })}
        />
      </Field>
      <Field label="Steuer-ID (optional)" error={err("steuer_id")}>
        {draft.steuerIdMode === "keep" && props.masked ? (
          <div className="flex flex-wrap items-center gap-2">
            <span className="num">{props.masked}</span>
            <button
              type="button"
              className={buttonClass}
              onClick={() => set({ steuerIdMode: "edit" })}
            >
              Ändern
            </button>
            <button
              type="button"
              className={buttonClass}
              onClick={() => set({ steuerIdMode: "remove" })}
            >
              Entfernen
            </button>
          </div>
        ) : draft.steuerIdMode === "remove" ? (
          <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
            <span>Wird beim Speichern entfernt</span>
            <button
              type="button"
              className={buttonClass}
              onClick={() => set({ steuerIdMode: "keep" })}
            >
              Behalten
            </button>
          </div>
        ) : (
          <input
            type="text"
            inputMode="numeric"
            autoComplete="off"
            className={inputClass}
            value={draft.steuerId}
            onChange={(e) => set({ steuerId: e.target.value })}
          />
        )}
      </Field>
      {props.kind === "adult" ? (
        <Field label="Religion" error={err("religion")}>
          <select
            className={inputClass}
            value={draft.religion}
            onChange={(e) => set({ religion: e.target.value })}
          >
            {props.labels.religions.map((r) => (
              <option key={r.code} value={r.code}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
      ) : null}
      <Field label="Grad der Behinderung" error={err("disability_grade")}>
        <select
          className={inputClass}
          value={draft.disability}
          onChange={(e) => set({ disability: e.target.value })}
        >
          <option value="">keiner</option>
          {DISABILITY_GRADES.map((g) => (
            <option key={g} value={g}>
              {g}
            </option>
          ))}
        </select>
      </Field>
    </div>
  );
}

/** Save a person draft: POST (new) or PATCH. Returns the person or the error to show. */
export async function savePerson(
  api: PersonDraftApi,
  opts: { person?: PersonOut; kind: "adult" | "child"; linkToMe?: boolean; jahr: number },
): Promise<PersonOut | SaveError> {
  const fields = api.fields();
  if ("message" in fields) return fields;
  try {
    const saved = opts.person
      ? await patchPerson(opts.person.id, fields)
      : await createPerson({
          ...fields,
          kind: opts.kind,
          ...(opts.linkToMe ? { link_to_me: true } : {}),
        });
    api.saved(saved); // clears the typed Steuer-ID
    return saved;
  } catch (error) {
    return saveError(error, { jahr: opts.jahr, name: fields.first_name });
  }
}

export function PersonForm(props: {
  person?: PersonOut;
  kind: "adult" | "child";
  linkToMe?: boolean;
  jahr: number;
  labels: HouseholdLabels;
  submitLabel: string;
  onSaved: (p: PersonOut) => void;
  onCancel?: () => void;
  extra?: ReactNode;
}) {
  const api = usePersonDraft(props.person);
  const [error, setError] = useState<SaveError | null>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    const result = await savePerson(api, {
      ...(props.person ? { person: props.person } : {}),
      kind: props.kind,
      ...(props.linkToMe ? { linkToMe: true } : {}),
      jahr: props.jahr,
    });
    setBusy(false);
    if ("message" in result) return setError(result);
    setError(null);
    toast.success(TEXT.saved);
    props.onSaved(result);
  };
  return (
    <form
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        void submit();
      }}
    >
      <PersonInputs
        api={api}
        kind={props.kind}
        masked={props.person?.steuer_id_masked ?? null}
        labels={props.labels}
        error={error}
      />
      <FormError error={error} />
      <div className="flex flex-col gap-2 sm:flex-row">
        {props.extra}
        {props.onCancel ? (
          <button type="button" className={buttonClass} onClick={props.onCancel}>
            Abbrechen
          </button>
        ) : null}
        <button type="submit" className={primaryClass} disabled={busy}>
          {props.submitLabel}
        </button>
      </div>
    </form>
  );
}

// --- employment ----------------------------------------------------------------------------

export function EmploymentForm(props: {
  personId: string;
  jahr: number;
  employment?: EmploymentOut;
  labels: HouseholdLabels;
  onSaved: (e: EmploymentOut) => void;
  onCancel: () => void;
}) {
  const e = props.employment;
  const [draft, setDraft] = useState({
    employer_name: e?.employer_name ?? "",
    steuerklasse: e?.steuerklasse ?? "1",
    has_factor: e?.has_factor ?? false,
    commute_km: e?.commute_km === null || e === undefined ? "" : String(e.commute_km),
    office_days: String(e?.office_days ?? 0),
    homeoffice_days: String(e?.homeoffice_days ?? 0),
  });
  const set = (patch: Partial<typeof draft>) => setDraft((d) => ({ ...d, ...patch }));
  const [error, setError] = useState<SaveError | null>(null);
  const [busy, setBusy] = useState(false);
  const err = (field: string) => errorAt(error, field);
  const submit = async () => {
    const fields = {
      employer_name: draft.employer_name,
      steuerklasse: draft.steuerklasse,
      has_factor: draft.steuerklasse === "4" && draft.has_factor,
      commute_km: draft.commute_km === "" ? null : Number(draft.commute_km),
      office_days: Number(draft.office_days || 0),
      homeoffice_days: Number(draft.homeoffice_days || 0),
    };
    setBusy(true);
    try {
      const saved = e
        ? await patchEmployment(e.id, fields)
        : await createEmployment(props.personId, props.jahr, fields);
      setError(null);
      toast.success(TEXT.saved);
      props.onSaved(saved);
    } catch (caught) {
      setError(saveError(caught, { jahr: props.jahr }));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="space-y-4 rounded-md border border-dashed p-4"
      onSubmit={(ev) => {
        ev.preventDefault();
        void submit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Arbeitgeber" error={err("employer_name")}>
          <input
            className={inputClass}
            value={draft.employer_name}
            onChange={(ev) => set({ employer_name: ev.target.value })}
          />
        </Field>
        <Field label="Steuerklasse" error={err("steuerklasse")}>
          <select
            className={inputClass}
            value={draft.steuerklasse}
            onChange={(ev) => set({ steuerklasse: ev.target.value })}
          >
            {props.labels.steuerklassen.map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}
              </option>
            ))}
          </select>
        </Field>
        {draft.steuerklasse === "4" ? (
          <label className="flex min-h-11 items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.has_factor}
              onChange={(ev) => set({ has_factor: ev.target.checked })}
            />
            Steuerklasse IV mit Faktor
          </label>
        ) : null}
        {err("has_factor") ? <p className="text-xs text-destructive">{err("has_factor")}</p> : null}
        <Field label="Einfache Entfernung in km" error={err("commute_km")}>
          <input
            type="number"
            min={0}
            max={999}
            className={inputClass}
            value={draft.commute_km}
            onChange={(ev) => set({ commute_km: ev.target.value })}
          />
        </Field>
        <Field label="Tage im Büro" error={err("office_days")}>
          <input
            type="number"
            min={0}
            className={inputClass}
            value={draft.office_days}
            onChange={(ev) => set({ office_days: ev.target.value })}
          />
        </Field>
        <Field label="Homeoffice-Tage" error={err("homeoffice_days")}>
          <input
            type="number"
            min={0}
            className={inputClass}
            value={draft.homeoffice_days}
            onChange={(ev) => set({ homeoffice_days: ev.target.value })}
          />
        </Field>
      </div>
      <FormError error={error} />
      <div className="flex flex-col gap-2 sm:flex-row">
        <button type="button" className={buttonClass} onClick={props.onCancel}>
          Abbrechen
        </button>
        <button type="submit" className={primaryClass} disabled={busy}>
          Speichern
        </button>
      </div>
    </form>
  );
}

// --- child year ----------------------------------------------------------------------------

const tooOld = new ApiError(422, "child_too_old");

export function ChildYearForm(props: {
  child: PersonOut;
  entry: ChildEntry;
  jahr: number;
  defaultShare: "full" | "half";
  labels: HouseholdLabels;
  onSaved: () => void;
}) {
  const row = props.entry.child_year;
  const [months, setMonths] = useState(String(row?.months ?? props.entry.max_months));
  const [share, setShare] = useState<string>(row?.allowance_share ?? props.defaultShare);
  const [inHousehold, setInHousehold] = useState(row?.in_household ?? true);
  const [error, setError] = useState<SaveError | null>(null);
  const [busy, setBusy] = useState(false);
  const ctx = { jahr: props.jahr, name: props.child.first_name, maxMonths: props.entry.max_months };
  if (props.entry.max_months === 0) {
    return <p className="text-sm text-muted-foreground">{saveError(tooOld, ctx).message}</p>;
  }
  const submit = async () => {
    setBusy(true);
    try {
      await putChildYear(props.jahr, props.child.id, {
        months: Number(months),
        allowance_share: share as "full" | "half",
        in_household: inHousehold,
      });
      setError(null);
      toast.success(TEXT.saved);
      props.onSaved();
    } catch (caught) {
      setError(saveError(caught, ctx));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="space-y-4"
      onSubmit={(ev) => {
        ev.preventDefault();
        void submit();
      }}
    >
      <div className="grid gap-4 sm:grid-cols-3">
        <Field label="Monate" error={error?.field === "months" ? error.message : null}>
          <input
            type="number"
            min={0}
            max={props.entry.max_months}
            className={inputClass}
            value={months}
            onChange={(ev) => setMonths(ev.target.value)}
          />
        </Field>
        <Field label="Freibetrag">
          <select className={inputClass} value={share} onChange={(ev) => setShare(ev.target.value)}>
            {props.labels.allowance_shares.map((s) => (
              <option key={s.code} value={s.code}>
                {s.label}
              </option>
            ))}
          </select>
        </Field>
        <label className="flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={inHousehold}
            onChange={(ev) => setInHousehold(ev.target.checked)}
          />
          Lebt im Haushalt
        </label>
      </div>
      {error && error.field !== "months" ? (
        <p role="alert" className="text-sm text-destructive">
          {error.message}
        </p>
      ) : null}
      <button type="submit" className={primaryClass} disabled={busy}>
        Speichern
      </button>
    </form>
  );
}

// --- profile -------------------------------------------------------------------------------

/** Step 2 / "Bearbeiten": Bundesland, Einzeln / Zusammen and the partner (existing or new). */
export function ProfileForm(props: {
  data: HouseholdOut;
  jahr: number;
  labels: HouseholdLabels;
  submitLabel: string;
  onSaved: (profile: ProfileOut) => void;
  onCancel?: () => void;
}) {
  const { data, jahr } = props;
  const adults = data.persons.filter((p) => p.kind === "adult");
  const taxpayer =
    data.profile?.taxpayer_person_id ?? adults.find((p) => p.is_me)?.id ?? adults[0]?.id ?? "";
  const others = adults.filter((p) => p.id !== taxpayer);
  const [bundesland, setBundesland] = useState(data.profile?.bundesland ?? "");
  const [joint, setJoint] = useState(data.profile?.filing_status === "joint");
  const [partner, setPartner] = useState(data.profile?.spouse_person_id ?? others[0]?.id ?? "new");
  const partnerDraft = usePersonDraft();
  const [error, setError] = useState<SaveError | null>(null);
  const [partnerError, setPartnerError] = useState<SaveError | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    try {
      let spouse: string | null = null;
      if (joint) {
        if (partner === "new") {
          const saved = await savePerson(partnerDraft, { kind: "adult", jahr });
          if ("message" in saved) return setPartnerError(saved);
          setPartnerError(null);
          setPartner(saved.id);
          spouse = saved.id;
        } else {
          spouse = partner;
        }
      }
      const profile = await putProfile(jahr, {
        filing_status: joint ? "joint" : "single",
        bundesland,
        taxpayer_person_id: taxpayer,
        spouse_person_id: spouse,
      });
      setError(null);
      toast.success(TEXT.saved);
      props.onSaved(profile);
    } catch (caught) {
      setError(saveError(caught, { jahr }));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form
      className="space-y-4"
      onSubmit={(ev) => {
        ev.preventDefault();
        void submit();
      }}
    >
      <Field
        label="Bundesland"
        hint={`Wohnsitz am 31.12.${jahr}`}
        error={errorAt(error, "bundesland")}
      >
        <select
          required
          className={inputClass}
          value={bundesland}
          onChange={(ev) => setBundesland(ev.target.value)}
        >
          <option value="" disabled>
            Bitte wählen
          </option>
          {props.labels.bundeslaender.map((b) => (
            <option key={b.code} value={b.code}>
              {b.label}
            </option>
          ))}
        </select>
      </Field>
      <fieldset className="flex flex-col gap-2 sm:flex-row">
        <legend className="mb-1 text-sm text-muted-foreground">Veranlagung</legend>
        {[
          [false, "Einzeln"],
          [true, "Zusammen"],
        ].map(([value, text]) => (
          <label
            key={String(text)}
            className="flex min-h-11 items-center gap-2 rounded-md border px-4 text-sm"
          >
            <input
              type="radio"
              name="veranlagung"
              checked={joint === value}
              onChange={() => setJoint(Boolean(value))}
            />
            {text}
          </label>
        ))}
      </fieldset>
      {joint ? (
        <div className="space-y-4 rounded-md border border-dashed p-4">
          <Field label="Partnerin / Partner" error={errorAt(error, "spouse_person_id")}>
            <select
              className={inputClass}
              value={partner}
              onChange={(ev) => setPartner(ev.target.value)}
            >
              {others.map((p) => (
                <option key={p.id} value={p.id}>
                  {[p.first_name, p.last_name].filter(Boolean).join(" ")}
                </option>
              ))}
              <option value="new">Neue Person erfassen</option>
            </select>
          </Field>
          {partner === "new" ? (
            <PersonInputs
              api={partnerDraft}
              kind="adult"
              masked={null}
              labels={props.labels}
              error={partnerError}
            />
          ) : null}
          <FormError error={partnerError} />
        </div>
      ) : null}
      <FormError error={error} />
      <div className="flex flex-col gap-2 sm:flex-row">
        {props.onCancel ? (
          <button type="button" className={buttonClass} onClick={props.onCancel}>
            {props.data.profile ? "Abbrechen" : "Zurück"}
          </button>
        ) : null}
        <button type="submit" className={primaryClass} disabled={busy}>
          {props.submitLabel}
        </button>
      </div>
    </form>
  );
}
