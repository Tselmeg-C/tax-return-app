import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginPage } from "@/components/auth/LoginPage";
import { PasskeyPrompt } from "@/components/auth/PasskeyPrompt";
import { SettingsPage } from "@/components/settings/SettingsPage";
import { PASSKEY_DISMISSED_KEY, PASSKEY_FAILED_TEXT } from "@/lib/passkeys";
import { validateLoginSearch } from "@/lib/login-search";
import { json, ME, mockFetch } from "@/test/fetch-mock";

class FakeCredential {
  toJSON() {
    return { id: "abc", rawId: "abc", type: "public-key", response: {} };
  }
  static parseRequestOptionsFromJSON(o: unknown) {
    return o;
  }
  static parseCreationOptionsFromJSON(o: unknown) {
    return o;
  }
}

function enableWebAuthn(credentials: { get?: () => unknown; create?: () => unknown }) {
  vi.stubGlobal("PublicKeyCredential", FakeCredential);
  Object.defineProperty(navigator, "credentials", { configurable: true, value: credentials });
}

function renderLogin(initial = "/login?next=%2Fbelege") {
  const root = createRootRoute({ component: Outlet });
  const tree = root.addChildren([
    createRoute({ getParentRoute: () => root, path: "/belege", component: () => <p>Belege</p> }),
    createRoute({
      getParentRoute: () => root,
      path: "/login",
      component: LoginPage,
      validateSearch: validateLoginSearch,
    }),
  ]);
  const router = createRouter({
    routeTree: tree,
    history: createMemoryHistory({ initialEntries: [initial] }),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return router;
}

const passkeyButton = () => screen.queryByRole("button", { name: "Mit Passkey anmelden" });

beforeEach(() => localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  Reflect.deleteProperty(navigator, "credentials");
});

describe("Mit Passkey anmelden", () => {
  it("is hidden without WebAuthn", async () => {
    mockFetch(() => json(404, {}));
    renderLogin();
    await screen.findByLabelText("E-Mail-Adresse");
    expect(passkeyButton()).toBeNull();
  });

  it("signs in through apiFetch and follows next", async () => {
    const get = vi.fn(async () => new FakeCredential());
    enableWebAuthn({ get });
    const { calls } = mockFetch((call) =>
      call.url.endsWith("/login/options")
        ? json(200, { challenge: "c" })
        : call.url.endsWith("/login/verify")
          ? json(200, { next: "/belege" })
          : json(401, {}),
    );
    // PublicKeyCredential instance check needs the stubbed class
    const router = renderLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Mit Passkey anmelden" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/belege"));
    expect(calls.map((c) => c.url)).toContain("/api/auth/passkey/login/verify");
    const verify = calls.find((c) => c.url.endsWith("/login/verify"));
    expect(verify?.headers.get("X-Requested-With")).toBe("belegbot");
    expect(verify?.body).toEqual({
      credential: new FakeCredential().toJSON(),
      next: "/belege",
    });
  });

  it("shows one generic message for any server failure", async () => {
    enableWebAuthn({ get: async () => new FakeCredential() });
    mockFetch((call) =>
      call.url.endsWith("/login/options")
        ? json(200, { challenge: "c" })
        : json(400, { detail: "invalid_passkey" }),
    );
    renderLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Mit Passkey anmelden" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(PASSKEY_FAILED_TEXT);
  });

  it("shows nothing when the user cancels", async () => {
    enableWebAuthn({
      get: async () => {
        throw new DOMException("cancelled", "NotAllowedError");
      },
    });
    mockFetch(() => json(200, { challenge: "c" }));
    renderLogin();
    fireEvent.click(await screen.findByRole("button", { name: "Mit Passkey anmelden" }));
    await waitFor(() => expect(passkeyButton()).toBeEnabled());
    expect(screen.queryByRole("alert")).toBeNull();
  });
});

function withClient(ui: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient()}>{ui}</QueryClientProvider>);
}

describe("PasskeyPrompt", () => {
  it("asks once and 'Später' is remembered in localStorage only", async () => {
    enableWebAuthn({});
    const { calls } = mockFetch(() => json(200, []));
    withClient(<PasskeyPrompt />);
    await screen.findByText("Passkey einrichten?");
    fireEvent.click(screen.getByRole("button", { name: "Später" }));
    expect(screen.queryByText("Passkey einrichten?")).toBeNull();
    expect(localStorage.getItem(PASSKEY_DISMISSED_KEY)).toBe("1");
    expect(calls.every((c) => c.method === "GET")).toBe(true);
  });

  it("stays away when a passkey exists, when dismissed, or without support", async () => {
    enableWebAuthn({});
    mockFetch(() =>
      json(200, [
        { id: "1", name: "Passkey 1", created_at: "2026-10-13T10:00:00Z", last_used_at: null },
      ]),
    );
    const view = withClient(<PasskeyPrompt />);
    await waitFor(() => expect(fetch).toHaveBeenCalled());
    expect(screen.queryByText("Passkey einrichten?")).toBeNull();
    view.unmount();

    mockFetch(() => json(200, []));
    localStorage.setItem(PASSKEY_DISMISSED_KEY, "1");
    withClient(<PasskeyPrompt />);
    await Promise.resolve();
    expect(screen.queryByText("Passkey einrichten?")).toBeNull();
  });
});

describe("Einstellungen", () => {
  it("lists passkeys, renames and deletes with confirm", async () => {
    enableWebAuthn({});
    let list = [
      { id: "p1", name: "Passkey 1", created_at: "2026-10-13T10:00:00Z", last_used_at: null },
    ];
    const { calls } = mockFetch((call) => {
      if (call.method === "PATCH") {
        list = [{ ...list[0]!, name: (call.body as { name: string }).name }];
        return json(200, list[0]);
      }
      if (call.method === "DELETE") {
        list = [];
        return new Response(null, { status: 204 });
      }
      if (call.url.endsWith("/auth/me")) return json(200, ME);
      return json(200, list);
    });
    const root = createRootRoute({ component: Outlet });
    const tree = root.addChildren([
      createRoute({ getParentRoute: () => root, path: "/", component: SettingsPage }),
      createRoute({ getParentRoute: () => root, path: "/einstellungen", component: () => null }),
    ]);
    const router = createRouter({ routeTree: tree, history: createMemoryHistory() });
    render(
      <QueryClientProvider client={new QueryClient()}>
        <RouterProvider router={router} />
      </QueryClientProvider>,
    );
    await screen.findByText("Passkey 1");
    fireEvent.click(screen.getByRole("button", { name: "Umbenennen" }));
    fireEvent.change(screen.getByLabelText("Name des Passkeys"), { target: { value: "Laptop" } });
    fireEvent.click(screen.getByRole("button", { name: "Speichern" }));
    await screen.findByText("Laptop");
    expect(calls.find((c) => c.method === "PATCH")?.url).toBe("/api/auth/passkeys/p1");

    vi.stubGlobal("confirm", () => true);
    fireEvent.click(screen.getByRole("button", { name: "Entfernen" }));
    await screen.findByText("Noch kein Passkey eingerichtet.");
    expect(calls.find((c) => c.method === "DELETE")?.headers.get("X-Requested-With")).toBe(
      "belegbot",
    );
  });
});
