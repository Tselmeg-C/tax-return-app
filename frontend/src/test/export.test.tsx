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
import { afterEach, describe, expect, it, vi } from "vitest";

import { toast } from "sonner";

import type { Summary } from "@/lib/summary";
import { belegeSearch } from "@/lib/taxItems";
import { Export } from "@/routes/_authed/export";
import { json, ME, mockFetch } from "@/test/fetch-mock";

vi.mock("sonner", () => ({
  toast: Object.assign(vi.fn(), { error: vi.fn(), warning: vi.fn() }),
  Toaster: () => null,
}));

const LABELS = {
  categories: [
    { code: "wk_arbeitsmittel", label: "Arbeitsmittel", group: "wk", group_label: "WK" },
  ],
  anlagen: [{ code: "n", label: "Anlage N" }],
  attention_reasons: [],
  payment_methods: [],
  doc_types: [],
  supported_years: [2025, 2026],
};

const SUMMARY = {
  year: 2025,
  status: "ok",
  by_anlage: [{ anlage: "n", amount: "120.50" }],
  by_line: [
    { anlage: "n", zeile: "54", category: "wk_arbeitsmittel", amount: "120.50", item_ids: ["a"] },
  ],
} as unknown as Summary;

function renderExport(summary: () => Response, extra?: (path: string) => Response | undefined) {
  const { calls } = mockFetch((call) => {
    const path = call.url.split("?")[0]!;
    if (path === "/api/auth/me") return json(200, ME);
    if (path === "/api/meta/labels") return json(200, LABELS);
    if (path === "/api/summary") return summary();
    return extra?.(path) ?? json(404, { detail: "not_found" });
  });
  const root = createRootRoute({ component: Outlet });
  const page = (p: string, component = () => <p>{p}</p>) =>
    createRoute({ getParentRoute: () => root, path: p, validateSearch: belegeSearch, component });
  const router = createRouter({
    routeTree: root.addChildren([page("/export", Export), page("/haushalt"), page("/belege")]),
    history: createMemoryHistory({ initialEntries: ["/export?jahr=2025"] }),
  });
  render(
    <QueryClientProvider
      client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
    >
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return calls;
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("Export", () => {
  it("links PDF and CSV to the export endpoints and shows the summary preview", async () => {
    renderExport(() => json(200, SUMMARY));
    const pdf = await screen.findByRole("link", { name: /PDF-Bericht/ });
    expect(pdf.getAttribute("href")).toBe("/api/export/2025.pdf");
    expect(pdf.hasAttribute("download")).toBe(true);
    expect(screen.getByRole("link", { name: /CSV/ }).getAttribute("href")).toBe(
      "/api/export/2025.csv",
    );
    expect(await screen.findByText("Arbeitsmittel")).toBeInTheDocument();
    expect(screen.getAllByText(/120,50/).length).toBeGreaterThan(0);
  });

  it("disables the exports without a profile and points to the Haushalt", async () => {
    renderExport(() => json(200, { ...SUMMARY, status: "no_profile", by_anlage: [], by_line: [] }));
    expect(await screen.findByText(/Erst Haushalt anlegen/)).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /PDF-Bericht/ })).toBeNull();
    expect(screen.getByRole("button", { name: /Belege-ZIP/ })).toBeDisabled();
  });

  it("warns when originals are missing from the ZIP", async () => {
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal(
      "URL",
      Object.assign(URL, { createObjectURL: () => "blob:x", revokeObjectURL: () => {} }),
    );
    renderExport(
      () => json(200, SUMMARY),
      (path) =>
        path === "/api/export/2025.zip"
          ? new Response("zip", { status: 200, headers: { "X-Export-Missing": "2" } })
          : undefined,
    );
    const button = await screen.findByRole("button", { name: /Belege-ZIP/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() =>
      expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining("2 Originale")),
    );
  });

  it("explains a 413", async () => {
    renderExport(
      () => json(200, SUMMARY),
      (path) =>
        path === "/api/export/2025.zip"
          ? json(413, { detail: "export_too_large", max_bytes: 500 * 1048576 })
          : undefined,
    );
    const button = await screen.findByRole("button", { name: /Belege-ZIP/ });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    await waitFor(() =>
      expect(toast.error).toHaveBeenCalledWith(expect.stringContaining("mehr als 500 MB")),
    );
  });
});
