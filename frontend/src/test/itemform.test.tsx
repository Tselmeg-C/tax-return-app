import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TaxItemList } from "@/components/documents/TaxItemList";
import { ItemForm } from "@/components/documents/ItemForm";
import { TEXT, type Labels, type TaxItemOut } from "@/lib/taxItems";
import { json, mockFetch } from "@/test/fetch-mock";

const LABELS: Labels = {
  categories: [
    { code: "wk_arbeitsmittel", label: "Arbeitsmittel", group: "wk", group_label: "WK" },
    {
      code: "handwerkerleistung",
      label: "Handwerker",
      group: "haushaltsnahe",
      group_label: "§35a",
    },
  ],
  anlagen: [],
  attention_reasons: [],
  payment_methods: [
    { code: "unknown", label: "Unbekannt" },
    { code: "cash", label: "Bar" },
    { code: "bank_transfer", label: "Überweisung" },
  ],
  doc_types: [],
  supported_years: [2025, 2026],
};

const ITEM: TaxItemOut = {
  id: "i-1",
  version: 3,
  document_id: "d-1",
  year: 2025,
  category: "wk_arbeitsmittel",
  anlage: "n",
  zeile: "42",
  gross_amount: "100.00",
  deductible_amount: "100.00",
  labour_share_35a: null,
  vendor: "Muster GmbH",
  invoice_date: null,
  payment_date: null,
  payment_method: "unknown",
  is_relevant: true,
  reason: null,
  confidence: null,
  overridden_by_user: false,
  person_id: null,
  created_at: "2026-01-05T10:00:00Z",
  updated_at: "2026-01-05T10:00:00Z",
  document: null,
};

function form(item: TaxItemOut | null, documentId: string | null) {
  const onSaved = vi.fn();
  render(
    <ItemForm
      item={item}
      documentId={documentId}
      year={2025}
      busy={false}
      labels={LABELS}
      persons={[]}
      onSaved={onSaved}
      onGone={vi.fn()}
      onCancel={vi.fn()}
    />,
  );
  return onSaved;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("ItemForm #61 fields", () => {
  it("sends only the changed new fields; dates are ISO from the date input", async () => {
    const { calls } = mockFetch(() => json(200, { ...ITEM, version: 4 }));
    const onSaved = form(ITEM, "d-1");
    expect(screen.getByLabelText("Rechnungsdatum")).toHaveAttribute("type", "date");
    // labels come from the labels payload
    expect(screen.getByRole("option", { name: "Überweisung" })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Rechnungsdatum"), { target: { value: "2025-03-01" } });
    fireEvent.change(screen.getByLabelText(/^Zahlungsart/), { target: { value: "bank_transfer" } });
    fireEvent.change(screen.getByLabelText("Anbieter"), { target: { value: "  Neu AG " } });
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0]!.body).toEqual({
      version: 3,
      vendor: "Neu AG",
      invoice_date: "2025-03-01",
      payment_method: "bank_transfer",
    });
  });

  it("hints when the payment year differs from the tax year, and on cash for § 35a", () => {
    mockFetch(() => json(200, ITEM));
    form({ ...ITEM, category: "handwerkerleistung", payment_date: "2024-12-30" }, "d-1");
    expect(screen.getByText(TEXT.paymentYear)).toBeInTheDocument();
    expect(screen.queryByText(TEXT.cashHint)).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Zahlungsart/), { target: { value: "cash" } });
    expect(screen.getByText(TEXT.cashHint)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/^Zahlungsdatum/), { target: { value: "2025-01-02" } });
    expect(screen.queryByText(TEXT.paymentYear)).not.toBeInTheDocument();
  });

  it("creates an item without a document: vendor required, POST /tax-items", async () => {
    const { calls } = mockFetch(() => json(201, ITEM));
    const onSaved = form(null, null);
    fireEvent.change(screen.getByLabelText("Brutto"), { target: { value: "50,00" } });
    fireEvent.change(screen.getByLabelText("Absetzbar"), { target: { value: "50,00" } });
    fireEvent.click(screen.getByText("Speichern"));
    expect(await screen.findByText(TEXT.vendorRequired)).toBeInTheDocument();
    expect(calls).toHaveLength(0);

    fireEvent.change(screen.getByLabelText("Name / Beschreibung"), {
      target: { value: "Verlorene Quittung" },
    });
    fireEvent.click(screen.getByText("Speichern"));
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(calls[0]!.url).toBe("/api/tax-items");
    expect(calls[0]!.method).toBe("POST");
    expect(calls[0]!.body).toMatchObject({
      vendor: "Verlorene Quittung",
      payment_method: "unknown",
      invoice_date: null,
      year: 2025,
    });
  });

  it("maps server codes to German texts without echoing values", async () => {
    mockFetch(() => json(422, { detail: "invalid_date", field: "payment_date" }));
    form(ITEM, "d-1");
    fireEvent.change(screen.getByLabelText(/^Zahlungsdatum/), { target: { value: "2025-01-02" } });
    fireEvent.click(screen.getByText("Speichern"));
    expect(await screen.findByText(TEXT.date)).toBeInTheDocument();
  });
});

describe("TaxItemList #61", () => {
  it("offers 'Ohne Beleg erfassen' and deletes only document-less items (conflict handled)", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls } = mockFetch((call) => {
      if (call.method === "DELETE")
        return json(409, { detail: "version_conflict", tax_item: ITEM });
      return json(200, { items: [ITEM], total: 1 });
    });
    render(
      <QueryClientProvider
        client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}
      >
        <TaxItemList jahr={2025} labels={LABELS} persons={[]} />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("ohne Beleg")).toBeInTheDocument();
    fireEvent.click(screen.getByText(TEXT.withoutDocument));
    expect(screen.getByLabelText("Name / Beschreibung")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Löschen"));
    expect(await screen.findByText(TEXT.conflict)).toBeInTheDocument();
    const del = calls.find((c) => c.method === "DELETE")!;
    expect(del.url).toBe("/api/tax-items/i-1?version=3");
  });
});
