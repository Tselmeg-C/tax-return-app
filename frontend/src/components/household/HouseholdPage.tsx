import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Baby, Plus, User } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { AppShell } from "@/components/AppShell";
import {
  buttonClass,
  ChildYearForm,
  EmploymentForm,
  PersonForm,
  primaryClass,
  ProfileForm,
} from "@/components/household/forms";
import {
  adultChildHint,
  copied,
  copyYear,
  deleteConfirm,
  deleteEmployment,
  deletePerson,
  fetchHousehold,
  fullName,
  householdKey,
  isChurchMember,
  notInReturn,
  saveError,
  TEXT,
  unsupportedYear,
  type EmploymentOut,
  type HouseholdLabels,
  type HouseholdOut,
  type PersonOut,
} from "@/lib/household";
import { label, PERSONS_KEY } from "@/lib/taxItems";
import { useYear } from "@/lib/year";

/** Haushalt (#13): the wizard while the year has no profile (or nothing after step 2),
 * else the page view. Everything is derived from `GET /household/{jahr}`. */
export function HouseholdPage() {
  const { jahr, supported, labels, select, isSupported } = useYear();
  const queryClient = useQueryClient();
  const household = useQuery({
    queryKey: householdKey(jahr),
    queryFn: () => fetchHousehold(jahr),
    enabled: Boolean(labels.data) && isSupported,
  });
  const [doneFor, setDoneFor] = useState<number | null>(null);
  // Once a year shows the wizard it stays until "Fertig" / copy (saving step 3 adds rows).
  const [wizardFor, setWizardFor] = useState<number | null>(null);
  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: householdKey(jahr) }),
      queryClient.invalidateQueries({ queryKey: PERSONS_KEY }),
    ]);
  };
  const allLabels = labels.data as HouseholdLabels | undefined;
  const data = household.data;

  let body;
  if (labels.isError || household.isError) {
    body = (
      <div className="mt-10 flex flex-wrap items-center gap-3 text-sm">
        <span className="text-destructive">{TEXT.loadError}</span>
        <button
          type="button"
          className={buttonClass}
          onClick={() => void (labels.isError ? labels.refetch() : household.refetch())}
        >
          {TEXT.retry}
        </button>
      </div>
    );
  } else if (allLabels && !isSupported) {
    body = <p className="mt-10 text-sm">{unsupportedYear(jahr, supported)}</p>;
  } else if (!allLabels || !data) {
    body = <p className="mt-10 text-sm text-muted-foreground">{TEXT.loading}</p>;
  } else {
    const anyRows = data.employments.length > 0 || data.children.some((c) => c.child_year);
    const needsWizard = !data.profile || (!anyRows && doneFor !== jahr);
    if (needsWizard && wizardFor !== jahr) setWizardFor(jahr);
    const wizard = needsWizard || (wizardFor === jahr && doneFor !== jahr);
    const props = { data, jahr, labels: allLabels, refresh };
    body = wizard ? (
      <Wizard key={jahr} {...props} onDone={() => setDoneFor(jahr)} />
    ) : (
      <PageView key={jahr} {...props} />
    );
  }

  return (
    <AppShell {...(select ? { year: select } : {})}>
      <h1 className="text-4xl">Haushalt</h1>
      {body}
    </AppShell>
  );
}

interface ViewProps {
  data: HouseholdOut;
  jahr: number;
  labels: HouseholdLabels;
  refresh: () => Promise<void>;
}

// --- wizard --------------------------------------------------------------------------------

function firstStep(data: HouseholdOut, offerCopy: boolean): number {
  if (!data.profile) {
    if (offerCopy) return 0;
    return data.persons.some((p) => p.is_me) ? 2 : 1;
  }
  return data.employments.length ? 4 : 3;
}

function Wizard(props: ViewProps & { onDone: () => void }) {
  const { data, jahr, labels, refresh } = props;
  const others = data.years_with_profile.filter((y) => y !== jahr);
  const from = others.length ? Math.max(...others) : null;
  const [step, setStep] = useState(() => firstStep(data, from !== null));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const me = data.persons.find((p) => p.is_me);

  const copy = async () => {
    if (from === null) return;
    setBusy(true);
    try {
      await copyYear(jahr, from);
      toast.success(copied(from));
      props.onDone();
      await refresh();
    } catch (caught) {
      setError(saveError(caught, { jahr }).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="sheet mt-6 space-y-6 p-4 sm:p-6">
      {step > 0 ? <p className="stamp text-muted-foreground">Schritt {step} von 4</p> : null}
      {step === 0 && from !== null ? (
        <div className="space-y-4">
          <p className="font-display text-xl">Steuerjahr {jahr} einrichten</p>
          <div className="flex flex-col gap-2 sm:flex-row">
            <button
              type="button"
              className={primaryClass}
              disabled={busy}
              onClick={() => void copy()}
            >
              Aus {from} übernehmen
            </button>
            <button type="button" className={buttonClass} onClick={() => setStep(me ? 2 : 1)}>
              Neu erfassen
            </button>
          </div>
          {error ? (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          ) : null}
        </div>
      ) : null}
      {step === 1 ? (
        <div className="space-y-4">
          <h2 className="text-2xl">Du</h2>
          <PersonForm
            {...(me ? { person: me } : { linkToMe: true })}
            kind="adult"
            jahr={jahr}
            labels={labels}
            submitLabel="Weiter"
            onSaved={() => {
              setStep(2);
              void refresh();
            }}
          />
        </div>
      ) : null}
      {step === 2 ? (
        <div className="space-y-4">
          <h2 className="text-2xl">Veranlagung</h2>
          <ProfileForm
            data={data}
            jahr={jahr}
            labels={labels}
            submitLabel="Weiter"
            cancelLabel="Zurück"
            onCancel={() => setStep(1)}
            onSaved={() => {
              setStep(3);
              void refresh();
            }}
          />
        </div>
      ) : null}
      {step === 3 ? (
        <div className="space-y-4">
          <h2 className="text-2xl">Arbeit</h2>
          {returnPersons(data).map((p) => (
            <Employments key={p.id} person={p} {...props} />
          ))}
          <StepNav onBack={() => setStep(2)} onNext={() => setStep(4)} next="Weiter" />
        </div>
      ) : null}
      {step === 4 ? (
        <div className="space-y-4">
          <h2 className="text-2xl">Kinder</h2>
          <Children {...props} />
          <StepNav onBack={() => setStep(3)} onNext={props.onDone} next="Fertig" />
        </div>
      ) : null}
    </section>
  );
}

function StepNav(props: { onBack: () => void; onNext: () => void; next: string }) {
  return (
    <div className="flex flex-col gap-2 sm:flex-row">
      <button type="button" className={buttonClass} onClick={props.onBack}>
        Zurück
      </button>
      <button type="button" className={primaryClass} onClick={props.onNext}>
        {props.next}
      </button>
    </div>
  );
}

const returnPersons = (data: HouseholdOut) => {
  const ids = [data.profile?.taxpayer_person_id, data.profile?.spouse_person_id];
  return data.persons.filter((p) => ids.includes(p.id));
};

// --- shared blocks -------------------------------------------------------------------------

function Employments(props: ViewProps & { person: PersonOut; showName?: boolean }) {
  const { person, data, jahr, labels, refresh, showName = true } = props;
  const jobs = data.employments.filter((e) => e.person_id === person.id);
  const [editing, setEditing] = useState<string | null>(null); // employment id or "new"
  const [none, setNone] = useState(false);
  const steuerklasse = (e: EmploymentOut) => label(labels.steuerklassen, e.steuerklasse);
  return (
    <div className="space-y-3">
      {showName ? <p className="font-display text-lg">{fullName(person)}</p> : null}
      <ul className="space-y-2">
        {jobs.map((e) =>
          editing === e.id ? (
            <li key={e.id}>
              <EmploymentForm
                personId={person.id}
                jahr={jahr}
                employment={e}
                labels={labels}
                onCancel={() => setEditing(null)}
                onSaved={() => {
                  setEditing(null);
                  void refresh();
                }}
              />
            </li>
          ) : (
            <li
              key={e.id}
              className={`rounded-md border p-3 text-sm ${e.in_return ? "" : "opacity-60"}`}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <span>
                  {e.employer_name} · Steuerklasse {steuerklasse(e)}
                  {e.has_factor ? " mit Faktor" : ""}
                </span>
                <span className="flex gap-2">
                  {e.in_return ? (
                    <button type="button" className={buttonClass} onClick={() => setEditing(e.id)}>
                      Bearbeiten
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className={buttonClass}
                    onClick={() =>
                      void deleteEmployment(e.id)
                        .then(refresh)
                        .catch(() => toast.error(TEXT.saveFailed))
                    }
                  >
                    Löschen
                  </button>
                </span>
              </div>
              <p className="num mt-1 text-xs text-muted-foreground">
                {e.commute_km ?? 0} km · {e.office_days} Bürotage · {e.homeoffice_days}{" "}
                Homeoffice-Tage
              </p>
              {e.in_return ? null : <p className="mt-1 text-xs">{notInReturn(jahr)}</p>}
            </li>
          ),
        )}
      </ul>
      {editing === "new" ? (
        <EmploymentForm
          personId={person.id}
          jahr={jahr}
          labels={labels}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      ) : jobs.some((e) => !e.in_return) && !jobs.some((e) => e.in_return) ? null : (
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <button type="button" className={buttonClass} onClick={() => setEditing("new")}>
            <Plus className="mr-1 inline h-4 w-4" />
            Arbeitgeber hinzufügen
          </button>
          {jobs.length === 0 ? (
            <label className="flex min-h-11 items-center gap-2 text-sm">
              <input type="checkbox" checked={none} onChange={(ev) => setNone(ev.target.checked)} />
              Keine Anstellung in {jahr}
            </label>
          ) : null}
        </div>
      )}
    </div>
  );
}

function Children(props: ViewProps) {
  const { data, jahr, labels, refresh } = props;
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const share = data.profile?.filing_status === "joint" ? "full" : "half";
  const persons = new Map(data.persons.map((p) => [p.id, p]));
  const remove = async (p: PersonOut) => {
    if (!window.confirm(deleteConfirm(fullName(p)))) return;
    try {
      await deletePerson(p.id);
      setError(null);
      await refresh();
    } catch (caught) {
      setError({ id: p.id, message: saveError(caught, { jahr, name: fullName(p) }).message });
    }
  };
  return (
    <div className="space-y-4">
      {data.children.map((entry) => {
        const child = persons.get(entry.person_id);
        if (!child) return null;
        return (
          <div key={child.id} className="sheet space-y-3 p-4">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="flex items-center gap-2 font-display text-lg">
                <Baby className="h-5 w-5" /> {fullName(child)}
              </span>
              <span className="flex flex-col gap-2 sm:flex-row">
                <button type="button" className={buttonClass} onClick={() => setEditing(child.id)}>
                  Bearbeiten
                </button>
                <button type="button" className={buttonClass} onClick={() => void remove(child)}>
                  Löschen
                </button>
              </span>
            </div>
            {error?.id === child.id ? (
              <p role="alert" className="text-sm text-destructive">
                {error.message}
              </p>
            ) : null}
            {editing === child.id ? (
              <PersonForm
                person={child}
                kind="child"
                jahr={jahr}
                labels={labels}
                submitLabel="Speichern"
                onCancel={() => setEditing(null)}
                onSaved={() => {
                  setEditing(null);
                  void refresh();
                }}
              />
            ) : null}
            {adultChildHint(child.dob, jahr) ? (
              <p className="text-xs text-muted-foreground">{TEXT.adultChild}</p>
            ) : null}
            <ChildYearForm
              key={`${entry.child_year?.months}-${entry.child_year?.allowance_share}`}
              child={child}
              entry={entry}
              jahr={jahr}
              defaultShare={share}
              labels={labels}
              onSaved={() => void refresh()}
            />
          </div>
        );
      })}
      {adding ? (
        <PersonForm
          kind="child"
          jahr={jahr}
          labels={labels}
          submitLabel="Kind speichern"
          onCancel={() => setAdding(false)}
          onSaved={() => {
            setAdding(false);
            void refresh();
          }}
        />
      ) : (
        <button type="button" className={buttonClass} onClick={() => setAdding(true)}>
          <Plus className="mr-1 inline h-4 w-4" />
          Kind hinzufügen
        </button>
      )}
    </div>
  );
}

// --- page view -----------------------------------------------------------------------------

function PageView(props: ViewProps) {
  const { data, jahr, labels, refresh } = props;
  const profile = data.profile!;
  const [editProfile, setEditProfile] = useState(false);
  const [childrenHint, setChildrenHint] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [adding, setAdding] = useState<"adult" | "child" | null>(null);
  const [error, setError] = useState<{ id: string; message: string } | null>(null);
  const inReturn = returnPersons(data);
  const adults = data.persons.filter((p) => p.kind === "adult");
  const outside = adults.filter((p) => !inReturn.includes(p));
  const members = inReturn.filter(isChurchMember).length;

  const remove = async (p: PersonOut) => {
    if (!window.confirm(deleteConfirm(fullName(p)))) return;
    try {
      await deletePerson(p.id);
      setError(null);
      await refresh();
    } catch (caught) {
      setError({ id: p.id, message: saveError(caught, { jahr, name: fullName(p) }).message });
    }
  };

  const adultCard = (p: PersonOut, active: boolean) => (
    <div key={p.id} className={`sheet space-y-4 p-4 sm:p-6 ${active ? "" : "opacity-60"}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary">
            <User className="h-5 w-5" />
          </span>
          <div>
            <p className="font-display text-xl">{fullName(p)}</p>
            <p className="num text-xs text-muted-foreground">
              {p.dob ? `geb. ${new Date(p.dob).toLocaleDateString("de-DE")}` : ""}
              {p.steuer_id_masked ? ` · Steuer-ID ${p.steuer_id_masked}` : ""}
            </p>
          </div>
        </div>
        <span className="flex w-full flex-col gap-2 sm:w-auto sm:flex-row">
          <button type="button" className={buttonClass} onClick={() => setEditing(p.id)}>
            Bearbeiten
          </button>
          <button type="button" className={buttonClass} onClick={() => void remove(p)}>
            Löschen
          </button>
        </span>
      </div>
      {active ? null : <p className="text-sm">{notInReturn(jahr)}</p>}
      {error?.id === p.id ? (
        <p role="alert" className="text-sm text-destructive">
          {error.message}
        </p>
      ) : null}
      {editing === p.id ? (
        <PersonForm
          person={p}
          kind="adult"
          jahr={jahr}
          labels={labels}
          submitLabel="Speichern"
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            void refresh();
          }}
        />
      ) : null}
      {active || data.employments.some((e) => e.person_id === p.id) ? (
        <Employments person={p} showName={false} {...props} />
      ) : null}
    </div>
  );

  return (
    <div className="mt-6 space-y-6">
      <section className="sheet space-y-4 p-4 sm:p-6">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-2xl">Veranlagung {jahr}</h2>
          {editProfile ? null : (
            <button type="button" className={buttonClass} onClick={() => setEditProfile(true)}>
              Bearbeiten
            </button>
          )}
        </div>
        {editProfile ? (
          <ProfileForm
            data={data}
            jahr={jahr}
            labels={labels}
            submitLabel="Speichern"
            onCancel={() => setEditProfile(false)}
            onSaved={(saved) => {
              setEditProfile(false);
              if (saved.filing_status !== profile.filing_status && data.children.length) {
                setChildrenHint(true);
              }
              void refresh();
            }}
          />
        ) : (
          <dl className="grid gap-4 sm:grid-cols-3">
            <div>
              <dt className="stamp text-muted-foreground">Veranlagung</dt>
              <dd className="mt-1">{label(labels.filing_statuses, profile.filing_status)}</dd>
            </div>
            <div>
              <dt className="stamp text-muted-foreground">Bundesland</dt>
              <dd className="mt-1">{label(labels.bundeslaender, profile.bundesland)}</dd>
            </div>
            <div>
              <dt className="stamp text-muted-foreground">Kirchensteuer</dt>
              <dd className="mt-1">
                {inReturn
                  .map((p) => `${p.first_name}: ${isChurchMember(p) ? "ja" : "nein"}`)
                  .join(", ")}
              </dd>
            </div>
          </dl>
        )}
        {profile.filing_status === "joint" && members === 1 ? (
          <p className="text-sm text-muted-foreground">{TEXT.churchHint}</p>
        ) : null}
        {childrenHint ? <p className="text-sm text-warning">{TEXT.childrenReview}</p> : null}
      </section>

      <div className="grid gap-5 md:grid-cols-2">
        {inReturn.map((p) => adultCard(p, true))}
        {outside.map((p) => adultCard(p, false))}
      </div>

      <section className="space-y-4">
        <h2 className="text-2xl">Kinder</h2>
        <Children {...props} />
      </section>

      {adding ? (
        <section className="sheet space-y-4 p-4 sm:p-6">
          <PersonForm
            kind={adding}
            jahr={jahr}
            labels={labels}
            submitLabel="Speichern"
            onCancel={() => setAdding(null)}
            onSaved={() => {
              setAdding(null);
              void refresh();
            }}
          />
        </section>
      ) : (
        <button type="button" className={primaryClass} onClick={() => setAdding("adult")}>
          <Plus className="mr-1 inline h-4 w-4" />
          Person hinzufügen
        </button>
      )}
    </div>
  );
}
