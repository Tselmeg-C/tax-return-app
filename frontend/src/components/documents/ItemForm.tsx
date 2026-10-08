import { useState } from "react";

import { ApiError } from "@/lib/api";
import { parseEuroInput, toEuroInput } from "@/lib/money";
import {
  createTaxItem,
  HOUSEHOLD_GROUP,
  patchTaxItem,
  saveError,
  TEXT,
  type ItemFields,
  type Labels,
  type Person,
  type TaxItemOut,
} from "@/lib/taxItems";

interface Draft {
  category: string;
  is_relevant: boolean;
  gross: string;
  deductible: string;
  labour: string;
  year: number;
  person: string; // "" = Haushalt
}

const draftOf = (item: TaxItemOut): Draft => ({
  category: item.category,
  is_relevant: item.is_relevant,
  gross: toEuroInput(item.gross_amount),
  deductible: toEuroInput(item.deductible_amount),
  labour: toEuroInput(item.labour_share_35a),
  year: item.year,
  person: item.person_id ?? "",
});

const isZero = (text: string) => {
  const value = parseEuroInput(text);
  return value === null || Number(value) === 0;
};

/**
 * Inline edit form (PATCH with only the changed fields + `version`) or, with `item = null`,
 * the manual item for a document (POST). German amounts are parsed by `parseEuroInput`.
 */
export function ItemForm(props: {
  item: TaxItemOut | null;
  documentId: string;
  year: number;
  busy: boolean;
  labels: Labels;
  persons: Person[];
  onSaved: (saved: TaxItemOut) => void;
  onGone: () => void;
  onCancel: () => void;
}) {
  const { labels, persons } = props;
  const [base, setBase] = useState(props.item);
  const [draft, setDraft] = useState<Draft>(() =>
    base
      ? draftOf(base)
      : {
          category: labels.categories[0]?.code ?? "",
          is_relevant: true,
          gross: "",
          deductible: "",
          labour: "",
          year: props.year,
          person: "",
        },
  );
  const [deductibleTouched, setDeductibleTouched] = useState(Boolean(base));
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const groupOf = (code: string) => labels.categories.find((c) => c.code === code)?.group;
  const household = groupOf(draft.category) === HOUSEHOLD_GROUP;
  const set = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch }));

  const setCategory = (category: string) => {
    const patch: Partial<Draft> = { category };
    if (category === "irrelevant") Object.assign(patch, { is_relevant: false, deductible: "0,00" });
    if (groupOf(category) === HOUSEHOLD_GROUP) patch.person = "";
    else patch.labour = "";
    set(patch);
  };

  const setRelevant = (on: boolean) => {
    if (!on) return set({ is_relevant: false, deductible: "0,00" });
    const prefill = household && draft.labour.trim() ? draft.labour : draft.gross;
    set({ is_relevant: true, ...(isZero(draft.deductible) ? { deductible: prefill } : {}) });
  };

  const groups = new Map<string, Labels["categories"]>();
  for (const c of labels.categories)
    groups.set(c.group_label, [...(groups.get(c.group_label) ?? []), c]);
  const years = labels.supported_years;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const gross = parseEuroInput(draft.gross);
    const deductible = parseEuroInput(draft.deductible);
    const labour = household && draft.labour.trim() ? parseEuroInput(draft.labour) : null;
    if (gross === null || deductible === null || (household && draft.labour.trim() && !labour)) {
      setError(TEXT.amount);
      return;
    }
    const fields: ItemFields = {
      category: draft.category,
      is_relevant: draft.is_relevant,
      gross_amount: gross,
      deductible_amount: deductible,
      labour_share_35a: labour,
      year: draft.year,
      person_id: household ? null : draft.person || null,
    };
    setSaving(true);
    setError(null);
    try {
      if (base) {
        const changed: Partial<ItemFields> = {};
        for (const key of Object.keys(fields) as (keyof ItemFields)[]) {
          if (fields[key] !== base[key]) Object.assign(changed, { [key]: fields[key] });
        }
        props.onSaved(await patchTaxItem(base.id, base.version, changed));
      } else {
        props.onSaved(await createTaxItem(props.documentId, fields));
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return props.onGone();
      const current = e instanceof ApiError && (e.body as { tax_item?: TaxItemOut })?.tax_item;
      if (e instanceof ApiError && e.detail === "version_conflict" && current) {
        setBase(current);
        setDraft(draftOf(current));
        setError(TEXT.conflict);
      } else {
        setError(saveError(e, draft.year, years));
      }
    } finally {
      setSaving(false);
    }
  };

  const field = "flex flex-col gap-1 text-sm";
  const input = "rounded border bg-background px-2 py-1";
  return (
    <form
      onSubmit={(e) => void submit(e)}
      className="w-full rounded-md border bg-card p-3"
      aria-label="Beleg bearbeiten"
    >
      {props.busy && <p className="mb-2 text-sm text-muted-foreground">{TEXT.processing}</p>}
      <fieldset disabled={props.busy || saving} className="grid gap-3 sm:grid-cols-2">
        <label className={field}>
          Kategorie
          <select
            className={input}
            value={draft.category}
            onChange={(e) => setCategory(e.target.value)}
          >
            {[...groups].map(([group, cats]) => (
              <optgroup key={group} label={group}>
                {cats.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.is_relevant}
            onChange={(e) => setRelevant(e.target.checked)}
          />
          Steuerlich relevant
        </label>
        <label className={field}>
          Brutto
          <input
            className={input}
            type="text"
            inputMode="decimal"
            value={draft.gross}
            onChange={(e) => set({ gross: e.target.value })}
          />
        </label>
        <label className={field}>
          Absetzbar
          <input
            className={input}
            type="text"
            inputMode="decimal"
            value={draft.deductible}
            onChange={(e) => {
              setDeductibleTouched(true);
              set({ deductible: e.target.value });
            }}
          />
        </label>
        {household && (
          <label className={field}>
            Lohnanteil (§35a)
            <input
              className={input}
              type="text"
              inputMode="decimal"
              value={draft.labour}
              onChange={(e) =>
                set({
                  labour: e.target.value,
                  ...(deductibleTouched ? {} : { deductible: e.target.value }),
                })
              }
            />
          </label>
        )}
        <label className={field}>
          Steuerjahr
          <select
            className={input}
            value={draft.year}
            onChange={(e) => set({ year: Number(e.target.value) })}
          >
            {!years.includes(draft.year) && (
              <option value={draft.year} disabled>
                {draft.year} (nicht unterstützt)
              </option>
            )}
            {years.map((y) => (
              <option key={y} value={y}>
                {y}
              </option>
            ))}
          </select>
        </label>
        <label className={field}>
          Person
          <select
            className={input}
            value={draft.person}
            disabled={household}
            onChange={(e) => set({ person: e.target.value })}
          >
            <option value="">Haushalt</option>
            {persons.map((p) => (
              <option key={p.id} value={p.id}>
                {[p.first_name, p.last_name].filter(Boolean).join(" ")}
              </option>
            ))}
          </select>
          {household && <span className="text-xs text-muted-foreground">{TEXT.householdOnly}</span>}
        </label>
      </fieldset>
      {error && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="submit"
          disabled={props.busy || saving}
          className="rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground"
        >
          Speichern
        </button>
        <button
          type="button"
          onClick={props.onCancel}
          className="rounded-md border px-3 py-1.5 text-sm"
        >
          Abbrechen
        </button>
      </div>
    </form>
  );
}
