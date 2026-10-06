import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
  type AnyRoute,
} from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { FunctionComponent } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/components/auth/LoginPage";
import { LoginSentPage, SENT_TEXT } from "@/components/auth/LoginSentPage";
import { VerifyPage } from "@/components/auth/VerifyPage";
import { RouteError } from "@/components/RouteError";
import { UserMenu } from "@/components/UserMenu";
import { ME_KEY, rememberLoginRequest } from "@/lib/auth";
import { INVALID_LINK_TEXT } from "@/lib/login";
import { validateLoginSearch } from "@/lib/login-search";
import { routeTree } from "@/routeTree.gen";
import { json, ME, mockFetch } from "@/test/fetch-mock";

/** A small router with the real page components (no root shell, so jsdom can render it). */
function renderPages(initial: string, queryClient = new QueryClient()) {
  const root = createRootRoute({ component: Outlet });
  const page = (path: string, component: FunctionComponent, extra: object = {}): AnyRoute =>
    createRoute({ getParentRoute: () => root, path, component, ...extra });
  const tree = root.addChildren([
    page("/", () => (
      <div>
        Startseite <UserMenu />
      </div>
    )),
    page("/belege", () => <div>Belege</div>),
    page("/login", LoginPage, { validateSearch: validateLoginSearch }),
    page("/login/sent", LoginSentPage),
    page("/login/verify", VerifyPage),
  ]);
  const router = createRouter({
    routeTree: tree,
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient };
}

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

describe("/login", () => {
  it("posts {email, next} and lands on /login/sent", async () => {
    const { calls } = mockFetch(() => json(202, { status: "sent" }));
    const { router } = renderPages("/login?next=%2Fbelege");
    fireEvent.change(await screen.findByLabelText("E-Mail-Adresse"), {
      target: { value: "owner@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Link senden" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login/sent"));
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/auth/magic-link");
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.body).toEqual({ email: "owner@example.com", next: "/belege" });
    expect(calls[0]?.headers.get("X-Requested-With")).toBe("belegbot");
  });

  it.each([
    [422, "invalid_email", "Bitte eine gültige E-Mail-Adresse eingeben"],
    [429, "rate_limited", "Zu viele Versuche, bitte später erneut versuchen"],
  ])("shows the text for %i", async (status, detail, text) => {
    mockFetch(() => json(status, { detail }));
    const { router } = renderPages("/login");
    fireEvent.change(await screen.findByLabelText("E-Mail-Adresse"), {
      target: { value: "foo" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Link senden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(text);
    expect(router.state.location.pathname).toBe("/login");
  });
});

describe("/login/sent", () => {
  it("disables 'Erneut senden' for 60 s and never claims the account exists", async () => {
    mockFetch(() => json(202, { status: "sent" }));
    rememberLoginRequest("owner@example.com", undefined);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    renderPages("/login/sent");
    await screen.findByText(SENT_TEXT);
    const button = () => screen.getByRole("button", { name: /Erneut senden/ });
    expect(button()).toBeDisabled();
    const tick = () =>
      act(async () => {
        await vi.advanceTimersByTimeAsync(1000);
      });
    for (let i = 0; i < 59; i++) await tick();
    expect(button()).toBeDisabled();
    await tick();
    expect(button()).toBeEnabled();

    fireEvent.click(button());
    expect(button()).toBeDisabled(); // restarts after each send
    expect(fetch).toHaveBeenCalledTimes(1);

    const text = document.body.textContent ?? "";
    expect(text).toContain("Falls diese Adresse");
    expect(text).not.toMatch(/existiert|registriert|gefunden|wurde gesendet/i);
    expect(screen.getByRole("link", { name: "Andere Adresse verwenden" })).toHaveAttribute(
      "href",
      "/login",
    );
  });
});

describe("/login/verify", () => {
  it("strips the fragment first and posts the token only on click", async () => {
    window.history.replaceState(null, "", "/login/verify#token=abc");
    let hashAtFirstCall: string | null = null;
    const { calls } = mockFetch(() => {
      hashAtFirstCall ??= window.location.hash;
      return json(200, { next: "/belege" });
    });
    const queryClient = new QueryClient();
    const invalidate = vi.spyOn(queryClient, "invalidateQueries");
    const { router } = renderPages("/login/verify", queryClient);

    const button = await screen.findByRole("button", { name: "Anmelden" });
    expect(window.location.hash).toBe("");
    expect(calls).toHaveLength(0);

    fireEvent.click(button);
    await waitFor(() => expect(router.state.location.pathname).toBe("/belege"));
    expect(hashAtFirstCall).toBe("");
    expect(calls).toHaveLength(1);
    expect(calls[0]?.url).toBe("/api/auth/verify");
    expect(calls[0]?.body).toEqual({ token: "abc" });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ME_KEY });
  });

  it("shows the error text and a link to /login on 400", async () => {
    window.history.replaceState(null, "", "/login/verify#token=abc");
    mockFetch(() => json(400, { detail: "invalid_or_expired" }));
    renderPages("/login/verify");
    fireEvent.click(await screen.findByRole("button", { name: "Anmelden" }));
    expect(await screen.findByText(INVALID_LINK_TEXT)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Neuen Link anfordern" })).toHaveAttribute(
      "href",
      "/login",
    );
  });

  it("shows the error text without any request when there is no fragment", async () => {
    window.history.replaceState(null, "", "/login/verify");
    const { calls } = mockFetch(() => json(200, { next: "/" }));
    renderPages("/login/verify");
    expect(await screen.findByText(INVALID_LINK_TEXT)).toBeInTheDocument();
    expect(calls).toHaveLength(0);
  });

  it("sets <meta name=referrer content=no-referrer>", async () => {
    const router = createRouter({ routeTree, context: { queryClient: new QueryClient() } });
    const route = router.routesById["/login/verify"];
    const head = await route.options.head?.({} as never);
    expect(head?.meta).toContainEqual({ name: "referrer", content: "no-referrer" });
  });
});

describe("user menu", () => {
  it("shows the e-mail; Abmelden posts logout, clears the cache and lands on /login", async () => {
    const { calls } = mockFetch((call) =>
      call.url === "/api/auth/me" ? json(200, ME) : new Response(null, { status: 204 }),
    );
    const queryClient = new QueryClient();
    queryClient.setQueryData(["documents"], ["cached"]);
    const { router } = renderPages("/", queryClient);
    fireEvent.click(await screen.findByRole("button", { name: ME.email }));
    fireEvent.click(screen.getByRole("menuitem", { name: /^Abmelden/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    const logout = calls.find((c) => c.url === "/api/auth/logout");
    expect(logout?.method).toBe("POST");
    expect(logout?.headers.get("X-Requested-With")).toBe("belegbot");
    expect(queryClient.getQueryData(["documents"])).toBeUndefined();
  });

  it("'Auf allen Geräten abmelden' posts logout-all", async () => {
    const { calls } = mockFetch((call) =>
      call.url === "/api/auth/me" ? json(200, ME) : new Response(null, { status: 204 }),
    );
    const { router } = renderPages("/");
    fireEvent.click(await screen.findByRole("button", { name: ME.email }));
    fireEvent.click(screen.getByRole("menuitem", { name: /allen Geräten/ }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/login"));
    expect(calls.some((c) => c.url === "/api/auth/logout-all" && c.method === "POST")).toBe(true);
  });
});

describe("guard error component", () => {
  it("the _authed route renders RouteError on a failed session check", () => {
    const router = createRouter({ routeTree, context: { queryClient: new QueryClient() } });
    expect(router.routesById["/_authed"].options.errorComponent).toBe(RouteError);
  });
});
