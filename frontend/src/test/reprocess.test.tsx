import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BelegePage } from "@/components/documents/BelegePage";
import { Toaster } from "@/components/ui/sonner";
import { REPROCESS, type DocumentOut } from "@/lib/documents";
import { belegeSearch, type Labels, type TaxItemOut } from "@/lib/taxItems";
import { json, ME, mockFetch, type Call } from "@/test/fetch-mock";

const LABELS: Labels = {
  categories: [
    { code: "wk_arbeitsmittel", label: "Arbeitsmittel", group: "werbungskosten", group_label: "W" },
  ],
  anlagen: [{ code: "n", label: "Anlage N" }],
  attention_reasons: [],
  payment_methods: [{ code: "unknown", label: "Unbekannt" }],
  doc_types: [],
  supported_years: [2025],
};

let seq = 0;
const nextId = () => `00000000-0000-4000-8000-${String((seq += 1)).padStart(12, "0")}`;

function doc(patch: Partial<DocumentOut> = {}): DocumentOut {
  return {
    id: nextId(),
    status: "done",
    original_filename: null,
    mime_type: "application/pdf",
    size_bytes: 10,
    channel: "web",
    doc_type: "generic_bill",
    error_kind: null,
    attention_reason: null,
    created_at: "2026-01-05T10:00:00Z",
    updated_at: "2026-01-05T10:00:00Z",
    ...patch,
  };
}

function item(vendor: string, patch: Partial<TaxItemOut> = {}, d: DocumentOut = doc()): TaxItemOut {
  return {
    id: nextId(),
    version: 1,
    document_id: d.id,
    year: 2025,
    category: "wk_arbeitsmittel",
    anlage: "n",
    zeile: "42",
    gross_amount: "10.00",
    deductible_amount: "10.00",
    labour_share_35a: null,
    vendor,
    invoice_date: null,
    payment_date: null,
    payment_method: "unknown",
    is_relevant: true,
    reason: null,
    confidence: "0.900",
    overridden_by_user: false,
    person_id: null,
    created_at: "2026-01-05T10:00:00Z",
    updated_at: "2026-01-05T10:00:00Z",
    ...patch,
    document: d,
  };
}

function serve(items: TaxItemOut[], role: "owner" | "member", post?: (c: Call) => Response) {
  return mockFetch((call) => {
    const path = call.url.split("?")[0];
    if (path === "/api/auth/me") return json(200, { ...ME, role });
    if (path === "/api/meta/labels") return json(200, LABELS);
    if (path === "/api/persons") return json(200, []);
    if (path === "/api/documents" && call.method === "GET") return json(200, { documents: [] });
    if (path === "/api/tax-items") return json(200, { items, total: items.length });
    if (call.method === "POST" && post) return post(call);
    return json(404, { detail: "not_found" });
  });
}

function renderPage() {
  const root = createRootRoute({
    component: () => (
      <>
        <Outlet />
        <Toaster />
      </>
    ),
  });
  const belege = createRoute({
    getParentRoute: () => root,
    path: "/belege",
    validateSearch: belegeSearch,
    component: BelegePage,
  });
  const router = createRouter({
    routeTree: root.addChildren([belege]),
    history: createMemoryHistory({ initialEntries: ["/belege?jahr=2025"] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const rowOf = async (text: string) => (await screen.findByText(text)).closest("li")!;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Neu verarbeiten", () => {
  it("single: confirms with the cost note, posts fresh=false, shows the result toast", async () => {
    const { calls } = serve([item("Eins GmbH")], "member", () =>
      json(202, { document: doc({ status: "queued" }) }),
    );
    renderPage();
    const row = await rowOf("Eins GmbH");
    fireEvent.click(within(row).getByRole("button", { name: /Neu verarbeiten/ }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain(REPROCESS.confirm(1));
    expect(dialog.textContent).toContain("Kosten verursachen");
    expect((within(dialog).getByRole("checkbox") as HTMLInputElement).checked).toBe(false);
    expect(posts(calls)).toHaveLength(0);
    fireEvent.click(within(dialog).getByRole("button", { name: REPROCESS.button }));
    await screen.findByText("1 in der Warteschlange, 0 übersprungen");
    const [call] = posts(calls);
    expect(call!.url).toMatch(/^\/api\/documents\/[\w-]+\/reprocess$/);
    expect(call!.body).toEqual({ fresh: false });
    expect(call!.headers.get("X-Requested-With")).toBe("belegbot");
  });

  it("hides the action for an overridden document and disables it while running", async () => {
    serve(
      [
        item("Manuell GmbH", { overridden_by_user: true }),
        item("Laufend GmbH", {}, doc({ status: "processing" })),
      ],
      "member",
    );
    renderPage();
    const manual = await rowOf("Manuell GmbH");
    expect(within(manual).queryByRole("button", { name: /Neu verarbeiten/ })).toBeNull();
    const running = await rowOf("Laufend GmbH");
    const button = within(running).getByRole("button", { name: /Neu verarbeiten/ });
    expect((button as HTMLButtonElement).disabled).toBe(true);
  });

  it("members get no bulk selection", async () => {
    serve([item("Eins GmbH")], "member");
    renderPage();
    await rowOf("Eins GmbH");
    expect(screen.queryAllByRole("checkbox")).toHaveLength(0);
  });

  it("owner bulk: select two, fresh checkbox, posts the ids and shows skipped", async () => {
    const items = [item("A GmbH"), item("B GmbH")];
    const { calls } = serve(items, "owner", () =>
      json(200, {
        queued: [items[0]!.document_id],
        skipped: [{ id: items[1]!.document_id, reason: "has_overrides" }],
      }),
    );
    renderPage();
    for (const name of ["A GmbH", "B GmbH"]) {
      fireEvent.click(within(await rowOf(name)).getByRole("checkbox"));
    }
    fireEvent.click(await screen.findByRole("button", { name: REPROCESS.selected(2) }));
    const dialog = await screen.findByRole("dialog");
    expect(dialog.textContent).toContain(REPROCESS.confirm(2));
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(within(dialog).getByRole("button", { name: REPROCESS.button }));
    await screen.findByText("1 in der Warteschlange, 1 übersprungen");
    const [call] = posts(calls);
    expect(call!.url).toBe("/api/documents/reprocess");
    expect(call!.body).toEqual({ document_ids: items.map((i) => i.document_id), fresh: true });
  });

  it("caps the selection at 25", async () => {
    const items = Array.from({ length: 26 }, (_, n) => item(`Firma ${n + 1} GmbH`));
    serve(items, "owner");
    renderPage();
    await rowOf("Firma 1 GmbH");
    const boxes = (await screen.findAllByRole("checkbox")) as HTMLInputElement[];
    expect(boxes).toHaveLength(26);
    for (const box of boxes.slice(0, 25)) fireEvent.click(box);
    await waitFor(() => expect(boxes[25]!.disabled).toBe(true));
    expect(screen.getByText(REPROCESS.max)).toBeTruthy();
  });

  it("shows a message for 429", async () => {
    serve([item("Eins GmbH")], "member", () => json(429, { detail: "rate_limited" }));
    renderPage();
    fireEvent.click(
      within(await rowOf("Eins GmbH")).getByRole("button", { name: /Neu verarbeiten/ }),
    );
    fireEvent.click(
      within(await screen.findByRole("dialog")).getByRole("button", { name: REPROCESS.button }),
    );
    await screen.findByText(REPROCESS.rateLimited);
  });
});
