import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowRight, CheckCircle2, Info } from "lucide-react";

import { AppShell } from "@/components/AppShell";
import { ApiError } from "@/lib/api";
import { formatEur } from "@/lib/money";
import {
  absMoney,
  fetchSummary,
  formatDate,
  isNachzahlung,
  SUMMARY_TEXT as T,
  summaryKey,
  type Pflicht,
  type Summary,
  type SummaryLabels,
} from "@/lib/summary";
import { fetchPersons, label, listTaxItems, PERSONS_KEY, TAX_ITEMS_KEY } from "@/lib/taxItems";
import { useYear } from "@/lib/year";

/** Übersicht (#17): everything comes from `GET /summary`; no number is computed here. */
export function Dashboard() {
  const { jahr, select, labels, isSupported } = useYear();
  const summary = useQuery({
    queryKey: summaryKey(jahr),
    queryFn: () => fetchSummary(jahr),
    enabled: Boolean(labels.data) && isSupported,
    retry: false,
  });
  const allLabels = labels.data as SummaryLabels | undefined;

  let body;
  if (allLabels && !isSupported) {
    body = <p className="mt-10 text-sm">{T.unsupportedYear(jahr)}</p>;
  } else if (summary.error instanceof ApiError && summary.error.detail === "unsupported_year") {
    body = <p className="mt-10 text-sm">{T.unsupportedYear(jahr)}</p>;
  } else if (labels.isError || summary.isError) {
    body = (
      <div className="mt-10 flex flex-wrap items-center gap-3 text-sm">
        <span role="alert" className="text-destructive">
          {T.loadError}
        </span>
        <button
          type="button"
          className="min-h-11 rounded-md border px-4 py-2 hover:bg-secondary"
          onClick={() => void (labels.isError ? labels.refetch() : summary.refetch())}
        >
          {T.retry}
        </button>
      </div>
    );
  } else if (!allLabels || !summary.data) {
    body = <p className="mt-10 text-sm text-muted-foreground">{T.loading}</p>;
  } else if (summary.data.status === "no_profile") {
    body = (
      <div className="sheet mt-10 space-y-3 p-6">
        <p>{T.noProfile}</p>
        <Link
          to="/haushalt"
          search={{ jahr }}
          className="inline-flex items-center gap-1 text-primary hover:underline"
        >
          {T.toHaushalt} <ArrowRight className="h-4 w-4" />
        </Link>
      </div>
    );
  } else {
    body = <Ready data={summary.data} jahr={jahr} labels={allLabels} />;
  }

  return <AppShell {...(select ? { year: select } : {})}>{body}</AppShell>;
}

function PflichtCard({ pflicht, labels }: { pflicht: Pflicht; labels: SummaryLabels }) {
  const mandatory = pflicht.state === "mandatory";
  return (
    <div className="sheet flex items-start gap-3 p-5">
      <CheckCircle2
        className={`mt-0.5 h-5 w-5 shrink-0 ${mandatory ? "text-warning" : "text-success"}`}
      />
      <div className="space-y-2">
        <p className="font-medium">Pflichtveranlagung</p>
        <p className="text-sm">{mandatory ? T.mandatory : T.voluntary}</p>
        {pflicht.reasons.length ? (
          <ul className="list-disc pl-4 text-sm text-muted-foreground">
            {pflicht.reasons.map((r) => (
              <li key={r}>{label(labels.pflicht_reasons, r)}</li>
            ))}
          </ul>
        ) : null}
        <p className="text-sm text-muted-foreground">
          {T.deadline} ({label(labels.pflicht_deadline_kinds, pflicht.deadline_kind)}):{" "}
          <span className="num">{formatDate(pflicht.deadline)}</span>
        </p>
        {pflicht.overdue ? (
          <p role="alert" className="text-sm text-destructive">
            {T.overdue}
          </p>
        ) : null}
        {pflicht.not_checked.length ? (
          <div className="text-xs text-muted-foreground">
            <p>{T.notChecked}:</p>
            <ul className="list-disc pl-4">
              {pflicht.not_checked.map((c) => (
                <li key={c}>{label(labels.pflicht_checks, c)}</li>
              ))}
            </ul>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function Ready({ data, jahr, labels }: { data: Summary; jahr: number; labels: SummaryLabels }) {
  const persons = useQuery({ queryKey: PERSONS_KEY, queryFn: fetchPersons });
  const recent = useQuery({
    queryKey: [...TAX_ITEMS_KEY, "recent", jahr],
    queryFn: () => listTaxItems({ year: jahr, filter: "relevant" }),
  });
  const name = (id: string | null) => persons.data?.find((p) => p.id === id)?.first_name;
  const refund = data.refund ?? "0.00";
  const nachzahlung = isNachzahlung(refund);
  const max = Math.max(...data.by_anlage.map((a) => Math.abs(Number(a.amount))), 0);
  const noteCodes = [...new Set(data.notes.map((n) => n.code))];

  return (
    <>
      <section className="mt-6 grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="sheet relative overflow-hidden p-8">
          <p className="stamp text-muted-foreground">Steuerjahr {jahr}</p>
          <h1 className="mt-4 text-lg font-normal text-muted-foreground">
            {nachzahlung ? T.nachzahlung : T.refund}
          </h1>
          <p className="num mt-1 text-6xl font-medium text-primary md:text-7xl">
            {formatEur(absMoney(refund))}
          </p>
          <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-dashed pt-5 text-sm">
            <div>
              <dt className="text-muted-foreground">zvE</dt>
              <dd className="num">{formatEur(data.zve ?? "0.00")}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Festgesetzte ESt</dt>
              <dd className="num">{formatEur(data.festzusetzende_est ?? "0.00")}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Lohnsteuer gezahlt</dt>
              <dd className="num">{formatEur(data.withheld?.lohnsteuer ?? "0.00")}</dd>
            </div>
          </dl>
          {data.provisional ? (
            <span className="stamp absolute right-6 top-6 rotate-6 rounded border-2 border-primary px-2 py-1 text-primary">
              {T.provisional}
            </span>
          ) : null}
        </div>

        <div className="flex flex-col gap-4">
          {data.pflicht ? <PflichtCard pflicht={data.pflicht} labels={labels} /> : null}
          <div className="sheet p-5">
            <p className="stamp mb-3 text-warning">Was fehlt noch</p>
            <ul className="space-y-3">
              {data.what_missing.map((m, i) => (
                <li key={`${m.code}-${m.person_id ?? i}`} className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <p className="text-sm font-medium">
                    {label(labels.estimate_missing, m.code)}
                    {name(m.person_id) ? ` (${name(m.person_id)})` : ""}
                    {m.code === "vorsorge_missing" ? (
                      <>
                        {" "}
                        <Link
                          to="/haushalt"
                          search={{ jahr }}
                          className="text-primary hover:underline"
                        >
                          eintragen
                        </Link>
                      </>
                    ) : null}
                  </p>
                </li>
              ))}
              {data.attention_count > 0 ? (
                <li className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <Link
                    to="/belege"
                    search={{ jahr }}
                    className="text-sm font-medium text-primary hover:underline"
                  >
                    {T.attention(data.attention_count)}
                  </Link>
                </li>
              ) : null}
              {!data.what_missing.length && data.attention_count === 0 ? (
                <li className="text-sm text-muted-foreground">{T.nothingMissing}</li>
              ) : null}
            </ul>
            {noteCodes.length ? (
              <ul className="mt-4 space-y-1 border-t border-dashed pt-3">
                {noteCodes.map((c) => (
                  <li key={c} className="flex gap-2 text-xs text-muted-foreground">
                    <Info className="mt-0.5 h-3 w-3 shrink-0" />
                    <span>
                      {label(labels.deduction_notes, c)}
                      {c === "vorsorge_estimated" ? (
                        <>
                          {" "}
                          <Link
                            to="/haushalt"
                            search={{ jahr }}
                            className="text-primary hover:underline"
                          >
                            {T.enterRealValues}
                          </Link>
                        </>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </div>
      </section>

      <section className="mt-10 grid gap-6 lg:grid-cols-2">
        <div className="sheet p-6">
          <h2 className="text-2xl">Summen je Anlage</h2>
          <ul className="mt-5 space-y-4">
            {data.by_anlage.map((a) => (
              <li key={a.anlage}>
                <div className="flex justify-between text-sm">
                  <span>{label(labels.anlagen, a.anlage)}</span>
                  <span className="num">{formatEur(a.amount)}</span>
                </div>
                <div className="mt-1.5 h-1.5 rounded-full bg-secondary">
                  <div
                    className="h-full rounded-full bg-primary"
                    style={{ width: `${max ? (Math.abs(Number(a.amount)) / max) * 100 : 0}%` }}
                  />
                </div>
              </li>
            ))}
          </ul>
        </div>
        <div className="sheet p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl">Zuletzt erkannt</h2>
            <Link
              to="/belege"
              search={{ jahr }}
              className="flex items-center gap-1 text-sm text-primary hover:underline"
            >
              Alle Belege <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <ul className="mt-4 divide-y divide-dashed">
            {(recent.data?.items ?? []).slice(0, 4).map((i) => (
              <li key={i.id} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium">
                    {i.vendor ?? label(labels.categories, i.category)}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {label(labels.categories, i.category)}
                    {i.anlage ? ` → ${label(labels.anlagen, i.anlage)}` : ""}
                    {i.zeile ? ` ${i.zeile}` : ""}
                  </p>
                </div>
                <span className="num text-sm">{formatEur(i.deductible_amount)}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </>
  );
}
