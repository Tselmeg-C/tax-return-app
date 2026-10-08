import { useQuery } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";

import { AppShell } from "@/components/AppShell";
import { DocumentUploads } from "@/components/documents/DocumentUploads";
import { TaxItemList } from "@/components/documents/TaxItemList";
import {
  defaultYear,
  fetchLabels,
  fetchPersons,
  LABELS_KEY,
  PERSONS_KEY,
  TEXT,
} from "@/lib/taxItems";

/** The Belege page on real data: upload area, "Hochgeladene Belege", "Belege {jahr}". */
export function BelegePage() {
  const search: { jahr?: number } = useSearch({ strict: false });
  const navigate = useNavigate();
  const labels = useQuery({ queryKey: LABELS_KEY, queryFn: fetchLabels, staleTime: Infinity });
  const persons = useQuery({ queryKey: PERSONS_KEY, queryFn: fetchPersons });
  const supported = labels.data?.supported_years ?? [];
  const jahr =
    search.jahr !== undefined && supported.includes(search.jahr)
      ? search.jahr
      : defaultYear(supported);
  const setYear = (year: number) => void navigate({ to: "/belege", search: { jahr: year } });
  const context = labels.data
    ? { jahr, labels: labels.data, persons: persons.data ?? [], onShowYear: setYear }
    : undefined;

  return (
    <AppShell
      {...(labels.data ? { year: { value: jahr, options: supported, onChange: setYear } } : {})}
    >
      <h1 className="text-4xl">Belege</h1>
      <DocumentUploads {...(context ? { belege: context } : {})} />
      {context ? (
        <TaxItemList jahr={jahr} labels={context.labels} persons={context.persons} />
      ) : labels.isError ? (
        <div className="mt-10 flex items-center gap-3 text-sm">
          <span className="text-destructive">{TEXT.loadError}</span>
          <button
            type="button"
            onClick={() => void labels.refetch()}
            className="rounded border px-2 py-1 hover:bg-secondary"
          >
            {TEXT.retry}
          </button>
        </div>
      ) : (
        <p className="mt-10 text-sm text-muted-foreground">{TEXT.loading}</p>
      )}
    </AppShell>
  );
}
