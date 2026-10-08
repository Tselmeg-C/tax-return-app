/**
 * The "Steuerjahr" of every page (#13): `?jahr=` in the URL, else the default year. The
 * AppShell select changes it on the current page; the nav links keep it.
 */
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useRouterState, useSearch } from "@tanstack/react-router";

import type { YearSelect } from "@/components/AppShell";
import { defaultYear, fetchLabels, LABELS_KEY } from "@/lib/taxItems";

export function useYear() {
  const search: { jahr?: number } = useSearch({ strict: false });
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const navigate = useNavigate();
  const labels = useQuery({ queryKey: LABELS_KEY, queryFn: fetchLabels, staleTime: Infinity });
  const supported = labels.data?.supported_years ?? [];
  const jahr = search.jahr ?? defaultYear(supported);
  const setYear = (year: number) => void navigate({ to: pathname, search: { jahr: year } });
  const select: YearSelect | undefined = labels.data
    ? {
        value: jahr,
        options: supported.includes(jahr) ? supported : [jahr, ...supported],
        onChange: setYear,
      }
    : undefined;
  return { jahr, supported, labels, select, isSupported: supported.includes(jahr) };
}
