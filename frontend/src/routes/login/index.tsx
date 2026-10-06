import { createFileRoute, redirect } from "@tanstack/react-router";

import { LoginPage } from "@/components/auth/LoginPage";
import { fetchMe, ME_KEY, ME_STALE_MS } from "@/lib/auth";
import { validateLoginSearch } from "@/lib/login-search";

export const Route = createFileRoute("/login/")({
  validateSearch: validateLoginSearch,
  head: () => ({ meta: [{ title: "Anmelden — belegbot" }] }),
  beforeLoad: async ({ context }) => {
    let signedIn = false;
    try {
      signedIn = Boolean(
        await context.queryClient.fetchQuery({
          queryKey: ME_KEY,
          queryFn: fetchMe,
          staleTime: ME_STALE_MS,
        }),
      );
    } catch {
      // api down: still show the login form
    }
    if (signedIn) throw redirect({ to: "/" });
  },
  component: LoginPage,
});
