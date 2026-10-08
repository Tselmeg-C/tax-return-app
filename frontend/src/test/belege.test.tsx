import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { BelegePage } from "@/components/documents/BelegePage";
import { Toaster } from "@/components/ui/sonner";
import type { DocumentOut } from "@/lib/documents";
import { belegeSearch, defaultYear, TEXT, type Labels, type TaxItemOut } from "@/lib/taxItems";
import { json, ME, mockFetch, type Call } from "@/test/fetch-mock";

const LABELS: Labels = {
  categories: [
    {
      code: "wk_arbeitsmittel",
      label: "Arbeitsmittel",
      group: "werbungskosten",
      group_label: "Werbungskosten",
    },
    {
      code: "krankheitskosten",
      label: "Krankheitskosten",
      group: "agb",
      group_label: "Außergewöhnliche Belastungen",
    },
    {
      code: "handwerkerleistung",
      label: "Handwerkerleistung",
      group: "haushaltsnahe",
      group_label: "Haushaltsnahe Aufwendungen (§35a)",
    },
    {
      code: "irrelevant",
      label: "Nicht steuerrelevant",
      group: "irrelevant",
      group_label: "Nicht steuerrelevant",
    },
  ],
  anlagen: [
    { code: "n", label: "Anlage N" },
    { code: "agb", label: "Anlage Außergewöhnliche Belastungen" },
  ],
  attention_reasons: [
    { code: "low_confidence", label: "Unsichere Erkennung" },
    { code: "multiple_documents", label: "Mehrere Belege in einer Datei" },
    { code: "doc_type_not_supported", label: "Dokumentart wird noch nicht ausgewertet" },
  ],
  payment_methods: [{ code: "unknown", label: "Unbekannt" }],
  doc_types: [{ code: "generic_bill", label: "Rechnung / Beleg" }],
  supported_years: [2025, 2026],
};
const PERSON = { id: "p-1", kind: "adult", first_name: "Alex", last_name: "Muster" } as const;

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

function item(patch: Partial<TaxItemOut> = {}): TaxItemOut {
  const document = patch.document === undefined ? doc() : patch.document;
  return {
    id: nextId(),
    version: 1,
    document_id: document?.id ?? null,
    year: 2025,
    category: "wk_arbeitsmittel",
    anlage: "n",
    zeile: "42",
    gross_amount: "312.40",
    deductible_amount: "312.40",
    labour_share_35a: null,
    vendor: "Bürobedarf Muster GmbH",
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
    document,
  };
}

interface Server {
  items?: (call: Call) => TaxItemOut[] | Response;
  documents?: DocumentOut[] | (() => DocumentOut[]);
  other?: (call: Call) => Response | undefined;
}

function serve(server: Server) {
  return mockFetch((call) => {
    const extra = server.other?.(call);
    if (extra) return extra;
    const path = call.url.split("?")[0];
    if (path === "/api/auth/me") return json(200, ME);
    if (path === "/api/meta/labels") return json(200, LABELS);
    if (path === "/api/persons") return json(200, [PERSON]);
    if (path === "/api/documents" && call.method === "GET") {
      const docs = typeof server.documents === "function" ? server.documents() : server.documents;
      return json(200, { documents: docs ?? [] });
    }
    if (path === "/api/tax-items" && call.method === "GET") {
      const result = server.items?.(call) ?? [];
      if (result instanceof Response) return result;
      return json(200, { items: result, total: result.length });
    }
    return json(404, { detail: "not_found" });
  });
}

function renderPage(path = "/belege?jahr=2025") {
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
    history: createMemoryHistory({ initialEntries: [path] }),
  });
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { router, queryClient };
}

const itemGets = (calls: Call[]) =>
  calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/tax-items"));
const rowOf = async (text: string) => (await screen.findByText(text)).closest("li")!;
const flat = (s: string | null | undefined) => (s ?? "").replace(/\s/g, " ");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("defaults", () => {
  it("defaults to the previous Berlin year if supported, else the highest", () => {
    expect(defaultYear([2025, 2026], new Date("2026-10-08T12:00:00Z"))).toBe(2025);
    expect(defaultYear([2025, 2026], new Date("2025-12-31T23:30:00Z"))).toBe(2025); // 2026 in Berlin
    expect(defaultYear([2025, 2026], new Date("2030-01-01T12:00:00Z"))).toBe(2026);
    expect(belegeSearch({ jahr: "2026" })).toEqual({ jahr: 2026 });
    expect(belegeSearch({ jahr: "x" })).toEqual({});
  });
});

describe("Belege list", () => {
  it("shows vendor, file name, person, labels, amounts and badges", async () => {
    const attentionDoc = doc({ status: "needs_attention", attention_reason: "low_confidence" });
    const { calls } = serve({
      items: () => [
        item({
          vendor: "Bürobedarf Muster GmbH",
          document: doc({ original_filename: "rechnung.pdf" }),
          person_id: PERSON.id,
          gross_amount: "1234.56",
          deductible_amount: "1234.56",
          overridden_by_user: true,
        }),
        item({
          vendor: null,
          document: attentionDoc,
          category: "krankheitskosten",
          anlage: "agb",
          zeile: null,
          confidence: "0.300",
        }),
        item({
          vendor: "Kein Bezug GmbH",
          document: doc({ original_filename: "kein.pdf" }),
          is_relevant: false,
          category: "irrelevant",
          anlage: null,
          zeile: null,
          deductible_amount: "0.00",
        }),
      ],
    });
    renderPage();
    const first = await rowOf("Bürobedarf Muster GmbH");
    expect(within(first).getByText("rechnung.pdf")).toBeInTheDocument();
    expect(within(first).getByText("Alex Muster")).toBeInTheDocument();
    expect(within(first).getByText("Arbeitsmittel")).toBeInTheDocument();
    expect(within(first).getByText("Anlage N · Zeile 42")).toBeInTheDocument();
    expect(flat(first.textContent)).toContain("1.234,56 €");
    expect(within(first).getByText("manuell")).toBeInTheDocument();
    expect(within(first).queryByText("unsicher")).toBeNull();
    expect(within(first).queryByText("Passt so")).toBeNull();

    const second = (await screen.findByText(/^Beleg vom /)).closest("li")!;
    expect(within(second).getByText("Haushalt")).toBeInTheDocument();
    expect(within(second).getByText("Anlage Außergewöhnliche Belastungen")).toBeInTheDocument();
    expect(within(second).getByText("unsicher")).toBeInTheDocument();
    expect(within(second).getByText("Unsichere Erkennung")).toBeInTheDocument();
    expect(within(second).getByText("Passt so")).toBeInTheDocument();
    expect(within(second).queryByText("manuell")).toBeNull();

    const third = await rowOf("Kein Bezug GmbH");
    expect(within(third).getByText("nicht steuerrelevant")).toBeInTheDocument();
    expect(itemGets(calls)[0]!.url).toBe("/api/tax-items?year=2025&limit=100");
  });

  it("sends only the changed field through apiFetch and rejects bad amounts", async () => {
    const one = item({ version: 3 });
    const { calls } = serve({
      items: () => [one],
      other: (call) =>
        call.method === "PATCH"
          ? json(200, { ...one, version: 4, deductible_amount: "12.50" })
          : undefined,
    });
    renderPage();
    const row = await rowOf("Bürobedarf Muster GmbH");
    fireEvent.click(within(row).getByText("Bearbeiten"));
    const input = within(row).getByLabelText("Absetzbar");
    fireEvent.change(input, { target: { value: "12,505" } });
    fireEvent.click(within(row).getByText("Speichern"));
    expect(await within(row).findByText(TEXT.amount)).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(0);

    fireEvent.change(input, { target: { value: "12,50" } });
    fireEvent.click(within(row).getByText("Speichern"));
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.url).toBe(`/api/tax-items/${one.id}`);
    expect(patch.headers.get("X-Requested-With")).toBe("belegbot");
    expect(patch.body).toEqual({ version: 3, deductible_amount: "12.50" });
    expect(await screen.findByText(TEXT.saved)).toBeInTheDocument();
  });

  it("409 version_conflict loads the current values; 404 refetches", async () => {
    const one = item();
    let answer = () =>
      json(409, {
        detail: "version_conflict",
        tax_item: { ...one, version: 2, deductible_amount: "99.00" },
      });
    const { calls } = serve({
      items: () => [one],
      other: (call) => (call.method === "PATCH" ? answer() : undefined),
    });
    renderPage();
    const row = await rowOf("Bürobedarf Muster GmbH");
    fireEvent.click(within(row).getByText("Bearbeiten"));
    fireEvent.change(within(row).getByLabelText("Absetzbar"), { target: { value: "10,00" } });
    fireEvent.click(within(row).getByText("Speichern"));
    expect(await within(row).findByText(TEXT.conflict)).toBeInTheDocument();
    expect(within(row).getByLabelText("Absetzbar")).toHaveValue("99,00");

    answer = () => json(404, { detail: "not_found" });
    const before = itemGets(calls).length;
    fireEvent.click(within(row).getByText("Speichern"));
    expect(await screen.findByText(TEXT.gone)).toBeInTheDocument();
    await waitFor(() => expect(itemGets(calls).length).toBeGreaterThan(before));
    // the second save was based on the conflict's version
    expect(calls.filter((c) => c.method === "PATCH")[1]!.body).toMatchObject({ version: 2 });
  });

  it.each([
    ["document_busy", () => json(409, { detail: "document_busy" }), TEXT.busy],
    ["500", () => json(500, {}), TEXT.saveFailed],
    ["network", () => Promise.reject(new TypeError("Failed to fetch")), TEXT.saveFailed],
  ])("%s keeps the form open with its text", async (_, fail, text) => {
    serve({
      items: () => [item()],
      other: (call) => (call.method === "PATCH" ? (fail() as Response) : undefined),
    });
    renderPage();
    const row = await rowOf("Bürobedarf Muster GmbH");
    fireEvent.click(within(row).getByText("Bearbeiten"));
    fireEvent.change(within(row).getByLabelText("Brutto"), { target: { value: "400" } });
    fireEvent.click(within(row).getByText("Speichern"));
    expect(await within(row).findByText(text)).toBeInTheDocument();
    expect(within(row).getByLabelText("Brutto")).toHaveValue("400");
  });

  it("shows loading, empty-year, empty-filter and error states", async () => {
    let mode: "empty" | "error" | "slow" = "slow";
    let release: () => void = () => {};
    const { calls } = serve({
      items: () => {
        if (mode === "error") return json(500, {});
        if (mode === "slow")
          return new Promise<Response>((resolve) => {
            release = () => resolve(json(200, { items: [], total: 0 }));
          }) as unknown as Response;
        return [];
      },
    });
    renderPage();
    expect(await screen.findByText(TEXT.loading)).toBeInTheDocument();
    mode = "empty";
    await act(async () => release());
    expect(
      await screen.findByText("Für 2025 gibt es noch keine Belege. Lade oben eine Rechnung hoch."),
    ).toBeInTheDocument();
    mode = "error";
    fireEvent.click(screen.getByText("Relevant"));
    expect(await screen.findByText(TEXT.loadError)).toBeInTheDocument();
    mode = "empty";
    const before = itemGets(calls).length;
    fireEvent.click(screen.getByText(TEXT.retry));
    expect(await screen.findByText(TEXT.emptyFilter)).toBeInTheDocument();
    expect(itemGets(calls).length).toBe(before + 1);
  });

  it("chips send the filter, 'Prüfen' shows a year column, the year select sets ?jahr", async () => {
    const { calls } = serve({ items: () => [item({ year: 2024 })] });
    const { router } = renderPage();
    await rowOf("Bürobedarf Muster GmbH");
    expect(screen.queryByText("Jahr")).toBeNull();
    for (const [chip, code] of [
      ["Relevant", "relevant"],
      ["Unsicher", "uncertain"],
      ["Manuell", "manual"],
      ["Prüfen", "attention"],
    ] as const) {
      fireEvent.click(screen.getByText(chip));
      await waitFor(() => expect(itemGets(calls).at(-1)!.url).toContain(`filter=${code}`));
    }
    expect(itemGets(calls).at(-1)!.url).toBe("/api/tax-items?filter=attention&limit=100");
    expect(await screen.findByText("Jahr")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Alle"));

    fireEvent.change(screen.getByLabelText("Steuerjahr"), { target: { value: "2026" } });
    await waitFor(() => expect(router.state.location.search).toEqual({ jahr: 2026 }));
    await waitFor(() =>
      expect(itemGets(calls).at(-1)!.url).toBe("/api/tax-items?year=2026&limit=100"),
    );
    expect(await screen.findByText("Belege 2026")).toBeInTheDocument();
  });

  it("'Mehr laden' fetches the next page", async () => {
    const page = [item(), item({ vendor: "Zweiter Anbieter" })];
    const { calls } = serve({
      items: (call) =>
        json(200, { items: call.url.includes("offset=1") ? [page[1]] : [page[0]], total: 2 }),
    });
    renderPage();
    await rowOf("Bürobedarf Muster GmbH");
    fireEvent.click(screen.getByText("Mehr laden"));
    expect(await screen.findByText("Zweiter Anbieter")).toBeInTheDocument();
    expect(itemGets(calls).at(-1)!.url).toContain("offset=1");
    expect(screen.queryByText("Mehr laden")).toBeNull();
  });

  it("'Passt so' sends only the version", async () => {
    const one = item({
      version: 5,
      document: doc({ status: "needs_attention", attention_reason: "low_confidence" }),
    });
    const { calls } = serve({
      items: () => [one],
      other: (call) => (call.method === "PATCH" ? json(200, { ...one, version: 6 }) : undefined),
    });
    renderPage();
    fireEvent.click(await screen.findByText("Passt so"));
    expect(await screen.findByText(TEXT.confirmed)).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")!.body).toEqual({ version: 5 });
  });

  it("warns about manual changes in the delete confirm", async () => {
    const edited = item({ overridden_by_user: true, vendor: "Handwerk Muster" });
    const { calls } = serve({
      items: () => [edited],
      other: (call) => (call.method === "DELETE" ? new Response(null, { status: 204 }) : undefined),
    });
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    const row = await rowOf("Handwerk Muster");
    fireEvent.click(within(row).getByText("Löschen"));
    expect(confirm).toHaveBeenCalledWith(
      "Beleg „Handwerk Muster“ löschen? Datei und erkannte Werte werden entfernt. Deine manuellen Änderungen gehen dabei verloren.",
    );
    await waitFor(() =>
      expect(
        calls.some(
          (c) => c.method === "DELETE" && c.url === `/api/documents/${edited.document_id}`,
        ),
      ).toBe(true),
    );
  });
});

describe("Hochgeladene Belege on the Belege page", () => {
  it("offers 'Manuell erfassen' for item-less documents and POSTs the form", async () => {
    const multi = doc({
      status: "needs_attention",
      attention_reason: "multiple_documents",
      original_filename: "zwei.pdf",
    });
    const official = doc({
      status: "needs_attention",
      attention_reason: "doc_type_not_supported",
      original_filename: "bescheid.pdf",
    });
    const { calls } = serve({
      documents: [multi, official],
      other: (call) =>
        call.method === "POST"
          ? json(201, item({ document: { ...multi, status: "done" } }))
          : undefined,
    });
    renderPage();
    const row = await rowOf("zwei.pdf");
    expect(within(row).getByText("Mehrere Belege in einer Datei")).toBeInTheDocument();
    expect(
      within(row).getByText(
        "Bitte jede Rechnung einzeln hochladen – oder die Werte manuell erfassen.",
      ),
    ).toBeInTheDocument();
    const officialRow = await rowOf("bescheid.pdf");
    expect(within(officialRow).queryByText("Manuell erfassen")).toBeNull();
    expect(
      within(officialRow).getByText(
        "Amtliche Dokumente werden bald unterstützt. Der Beleg bleibt gespeichert.",
      ),
    ).toBeInTheDocument();
    expect(within(officialRow).getByText("Original öffnen")).toBeInTheDocument();

    fireEvent.click(within(row).getByText("Manuell erfassen"));
    fireEvent.change(within(row).getByLabelText("Brutto"), { target: { value: "1.234,56" } });
    fireEvent.change(within(row).getByLabelText("Absetzbar"), { target: { value: "100" } });
    fireEvent.click(within(row).getByText("Speichern"));
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    const post = calls.find((c) => c.method === "POST")!;
    expect(post.url).toBe(`/api/documents/${multi.id}/tax-items`);
    expect(post.headers.get("X-Requested-With")).toBe("belegbot");
    expect(post.body).toEqual({
      category: "wk_arbeitsmittel",
      is_relevant: true,
      gross_amount: "1234.56",
      deductible_amount: "100.00",
      labour_share_35a: null,
      year: 2025,
      person_id: null,
    });
    expect(await screen.findByText(TEXT.created)).toBeInTheDocument();
  });

  it("toasts 'Beleg verarbeitet' with the year when a running row leaves the list", async () => {
    const running = doc({ status: "processing", original_filename: "neu.pdf" });
    let listed = [running];
    const { calls } = serve({
      documents: () => listed,
      items: (call) => (call.url.includes("document_id=") ? [item({ year: 2026 })] : []),
    });
    const { queryClient, router } = renderPage();
    await rowOf("neu.pdf");
    listed = [];
    await act(() => queryClient.invalidateQueries({ queryKey: ["documents"] }));
    expect(await screen.findByText("Beleg verarbeitet – Steuerjahr 2026")).toBeInTheDocument();
    expect(itemGets(calls).some((c) => c.url.includes(`document_id=${running.id}`))).toBe(true);
    fireEvent.click(screen.getByText("Anzeigen"));
    await waitFor(() => expect(router.state.location.search).toEqual({ jahr: 2026 }));
  });
});
