import { QueryClient } from "@tanstack/react-query";
import { createMemoryHistory, createRouter } from "@tanstack/react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import { routeTree } from "@/routeTree.gen";
import { json, ME, mockFetch } from "@/test/fetch-mock";

// Runs the real route tree's `beforeLoad`s without rendering (see app-routing.test.tsx).
function makeRouter(path: string) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const router = createRouter({
    routeTree,
    context: { queryClient },
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  return { router, queryClient };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("_authed guard", () => {
  it("redirects to /login?next=%2Fbelege when me is 401", async () => {
    mockFetch(() => json(401, { detail: "not_authenticated" }));
    const { router } = makeRouter("/belege");
    await router.load();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.search).toEqual({ next: "/belege" });
    expect(router.state.location.href).toBe("/login?next=%2Fbelege");
  });

  it("shows the error (no redirect) when me is 500", async () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    mockFetch(() => json(500, { detail: "boom" }));
    const { router } = makeRouter("/belege");
    await router.load();
    expect(router.state.location.pathname).toBe("/belege");
    const authed = router.state.matches.find((m) => m.routeId === "/_authed");
    expect(authed?.status).toBe("error");
  });

  it("lets a signed-in user through, and /login sends them to /", async () => {
    const { calls } = mockFetch((call) =>
      call.url === "/api/auth/me" ? json(200, ME) : json(404, {}),
    );
    const { router } = makeRouter("/belege");
    await router.load();
    expect(router.state.location.pathname).toBe("/belege");
    expect(router.state.matches.at(-1)?.routeId).toBe("/_authed/belege");
    expect(calls.every((c) => c.headers.get("X-Requested-With") === "belegbot")).toBe(true);

    await router.navigate({ to: "/login" });
    expect(router.state.location.pathname).toBe("/");
  });

  it("after logout (cache cleared) the back button leads to the login redirect", async () => {
    let signedIn = true;
    mockFetch(() => (signedIn ? json(200, ME) : json(401, { detail: "not_authenticated" })));
    const { router, queryClient } = makeRouter("/belege");
    await router.load();
    expect(router.state.location.pathname).toBe("/belege");

    // What the user menu does on "Abmelden".
    signedIn = false;
    queryClient.clear();
    await router.navigate({ to: "/login" });
    expect(router.state.location.pathname).toBe("/login");

    // RouterProvider normally subscribes the router to history changes.
    const unsubscribe = router.history.subscribe(() => void router.load());
    router.history.back();
    await vi.waitFor(() => {
      expect(router.state.location.href).toBe("/login?next=%2Fbelege");
    });
    unsubscribe();
  });

  it("keeps the public routes outside the guard", () => {
    const { router } = makeRouter("/");
    for (const path of ["/login", "/login/sent", "/login/verify", "/healthz", "/api/health"]) {
      const ids = router.matchRoutes(path).map((m) => m.routeId);
      expect(ids).not.toContain("/_authed");
    }
    expect(router.matchRoutes("/haushalt").map((m) => m.routeId)).toContain("/_authed");
  });
});
