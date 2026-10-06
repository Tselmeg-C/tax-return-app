import { QueryClient } from "@tanstack/react-query";
import { createRouter } from "@tanstack/react-router";

import { setUnauthorizedHandler } from "@/lib/api";
import { ME_KEY } from "@/lib/auth";
import { routeTree } from "./routeTree.gen";

export const getRouter = () => {
  const queryClient = new QueryClient();

  const router = createRouter({
    routeTree,
    context: { queryClient },
    scrollRestoration: true,
    defaultPreloadStaleTime: 0,
  });

  if (typeof window !== "undefined") {
    // Any 401 from apiFetch: the session is gone → mark `me` stale and go to the login page.
    setUnauthorizedHandler((next) => {
      void queryClient.invalidateQueries({ queryKey: ME_KEY, refetchType: "none" });
      void router.navigate({ to: "/login", search: { next } });
    });
  }

  return router;
};
