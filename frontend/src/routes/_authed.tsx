import { createFileRoute, Outlet, redirect } from "@tanstack/react-router";

import { RouteError } from "@/components/RouteError";
import { fetchMe, ME_KEY, ME_STALE_MS } from "@/lib/auth";

/**
 * Pathless layout for every app page (`/`, `/belege`, `/haushalt`, `/export`): no session →
 * redirect to `/login?next=<path>` (also during SSR). A failing session check (5xx, network)
 * shows the error page instead of logging the user out.
 */
export const Route = createFileRoute("/_authed")({
  beforeLoad: async ({ context, location }) => {
    const me = await context.queryClient.fetchQuery({
      queryKey: ME_KEY,
      queryFn: fetchMe,
      staleTime: ME_STALE_MS,
    });
    if (!me) {
      throw redirect({
        to: "/login",
        search: { next: `${location.pathname}${location.searchStr}` },
      });
    }
    return { me };
  },
  errorComponent: RouteError,
  component: Outlet,
});
