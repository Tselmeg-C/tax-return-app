import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createMemoryHistory,
  createRootRoute,
  createRoute,
  createRouter,
  Outlet,
  RouterProvider,
} from "@tanstack/react-router";
import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Dashboard } from "@/components/Dashboard";
import type { Summary, SummaryLabels } from "@/lib/summary";
import { belegeSearch } from "@/lib/taxItems";
import { json, ME, mockFetch } from "@/test/fetch-mock";

const coded = (pairs: [string, string][]) => pairs.map(([code, label]) => ({ code, label }));
const LABELS: SummaryLabels = {
  categories: [
    { code: "wk_arbeitsmittel", label: "Arbeitsmittel", group: "wk", group_label: "WK" },
  ],
  anlagen: coded([
    ["n", "Anlage N"],
    ["sonderausgaben", "Sonderausgaben"],
  ]),
  attention_reasons: [],
  payment_methods: [],
  doc_types: [],
  supported_years: [2025, 2026],
  deduction_notes: coded([["included_unreviewed", "Ungeprüfte Belege sind enthalten"]]),
  estimate_missing: coded([
    ["no_lstb", "Lohnsteuerbescheinigung fehlt"],
    ["vorsorge_missing", "Vorsorgeaufwendungen fehlen"],
    ["kap_v_not_supported", "Anlage KAP / V wird noch nicht berechnet"],
  ]),
  pflicht_reasons: coded([["steuerklasse_combination", "Steuerklassen III und V"]]),
  pflicht_checks: coded([["kv_refund", "Beitragserstattung der Krankenversicherung"]]),
  pflicht_deadline_kinds: coded([["self", "ohne Steuerberater"]]),
};

const SUMMARY: Summary = {
  year: 2025,
  status: "ok",
  params_version: "2025-1",
  provisional: false,
  provisional_sections: [],
  refund: "2184.37",
  zve: "98420.00",
  zve_before_children: "98420.00",
  gde: "0.00",
  tarifliche_est: "18631.00",
  ermaessigung_35a: "0.00",
  festzusetzende_est: "18631.00",
  soli: "0.00",
  kirchensteuer: "0.00",
  kindergeld_added: "0.00",
  withheld: { lohnsteuer: "20815.37", soli: "0.00", kirchensteuer: "0.00" },
  by_anlage: [
    { anlage: "n", amount: "1000.00" },
    { anlage: "sonderausgaben", amount: "500.00" },
  ],
  pflicht: {
    state: "voluntary",
    reasons: [],
    not_checked: ["kv_refund"],
    deadline_statutory: "2026-07-31",
    deadline: "2026-08-03",
    deadline_kind: "self",
    overdue: false,
  },
  what_missing: [],
  notes: [],
  attention_count: 0,
};

function renderDashboard(respond: (path: string) => Response, path = "/?jahr=2025") {
  const { calls } = mockFetch((call) => respond(call.url.split("?")[0]!));
  const root = createRootRoute({ component: Outlet });
  const page = (p: string, component = () => <p>{p}</p>) =>
    createRoute({ getParentRoute: () => root, path: p, validateSearch: belegeSearch, component });
  const router = createRouter({
    routeTree: root.addChildren([
      page("/", Dashboard),
      page("/belege"),
      page("/haushalt"),
      page("/export"),
    ]),
    history: createMemoryHistory({ initialEntries: [path] }),
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

const serve = (summary: () => Response) => (path: string) => {
  if (path === "/api/auth/me") return json(200, ME);
  if (path === "/api/meta/labels") return json(200, LABELS);
  if (path === "/api/persons") return json(200, [{ id: "p1", kind: "adult", first_name: "Anna" }]);
  if (path === "/api/summary") return summary();
  if (path === "/api/tax-items") return json(200, { items: [], total: 0 });
  return json(404, { detail: "not_found" });
};

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Übersicht", () => {
  it("shows a loading state first", async () => {
    renderDashboard(serve(() => json(200, SUMMARY)));
    expect(await screen.findByText(/Übersicht wird geladen/)).toBeInTheDocument();
    expect(await screen.findByText("Voraussichtliche Erstattung")).toBeInTheDocument();
  });

  it("renders the ok state from /summary without a provisional stamp", async () => {
    const calls = renderDashboard(serve(() => json(200, SUMMARY)));
    expect(await screen.findByText("Voraussichtliche Erstattung")).toBeInTheDocument();
    expect(screen.getByText(/2\.184,37/)).toBeInTheDocument();
    expect(screen.getByText(/98\.420,00/)).toBeInTheDocument();
    expect(screen.getByText("Anlage N")).toBeInTheDocument();
    expect(screen.queryByText("vorläufig")).toBeNull();
    expect(calls.some((c) => c.url === "/api/summary?year=2025")).toBe(true);
  });

  it("words a negative refund as Nachzahlung", async () => {
    renderDashboard(serve(() => json(200, { ...SUMMARY, refund: "-310.50" })));
    expect(await screen.findByText("Voraussichtliche Nachzahlung")).toBeInTheDocument();
    expect(screen.queryByText("Voraussichtliche Erstattung")).toBeNull();
    expect(screen.getByText(/310,50/)).toBeInTheDocument();
    expect(screen.queryByText(/-310/)).toBeNull();
  });

  it("stamps vorläufig when a params section is provisional", async () => {
    renderDashboard(
      serve(() => json(200, { ...SUMMARY, provisional: true, provisional_sections: ["tariff"] })),
    );
    expect(await screen.findByText("vorläufig")).toBeInTheDocument();
  });

  it("words a voluntary return as 'nach deinen Angaben' with not-checked list and deadline", async () => {
    renderDashboard(serve(() => json(200, SUMMARY)));
    expect(
      await screen.findByText(/Nach deinen Angaben besteht keine Pflicht/),
    ).toBeInTheDocument();
    expect(screen.getByText("Beitragserstattung der Krankenversicherung")).toBeInTheDocument();
    expect(screen.getByText("03.08.2026")).toBeInTheDocument();
    expect(screen.queryByText(/Frist abgelaufen/)).toBeNull();
  });

  it("shows a mandatory return with reasons and the overdue state", async () => {
    const pflicht = {
      ...SUMMARY.pflicht!,
      state: "mandatory" as const,
      reasons: ["steuerklasse_combination"],
      not_checked: [],
      overdue: true,
    };
    renderDashboard(serve(() => json(200, { ...SUMMARY, pflicht })));
    expect(
      await screen.findByText(/Nach deinen Angaben ist die Abgabe Pflicht/),
    ).toBeInTheDocument();
    expect(screen.getByText("Steuerklassen III und V")).toBeInTheDocument();
    expect(screen.getByText("Frist abgelaufen")).toBeInTheDocument();
  });

  it("lists what is missing, with the person name and the attention link", async () => {
    renderDashboard(
      serve(() =>
        json(200, {
          ...SUMMARY,
          what_missing: [
            { code: "no_lstb", person_id: "p1" },
            { code: "vorsorge_missing", person_id: null },
          ],
          attention_count: 3,
        }),
      ),
    );
    expect(await screen.findByText("Lohnsteuerbescheinigung fehlt (Anna)")).toBeInTheDocument();
    expect(screen.getByText("Vorsorgeaufwendungen fehlen")).toBeInTheDocument();
    const link = screen.getByText("3 Belege brauchen deine Aufmerksamkeit");
    expect(link.closest("a")?.getAttribute("href")).toBe("/belege?jahr=2025");
  });

  it("links to Haushalt when there is no profile", async () => {
    renderDashboard(
      serve(() =>
        json(200, {
          ...SUMMARY,
          status: "no_profile",
          refund: null,
          zve: null,
          pflicht: null,
          by_anlage: [],
          withheld: null,
        }),
      ),
    );
    const link = await screen.findByText(/Zum Haushalt/);
    expect(link.closest("a")?.getAttribute("href")).toBe("/haushalt?jahr=2025");
  });

  it("shows 'Steuerjahr nicht unterstützt' on 422 unsupported_year", async () => {
    renderDashboard(
      serve(() => json(422, { detail: "unsupported_year", field: "year" })),
      "/?jahr=2025",
    );
    expect(await screen.findByText(/Steuerjahr nicht unterstützt/)).toBeInTheDocument();
  });

  it("shows 'Steuerjahr nicht unterstützt' for a year without params and does not fetch", async () => {
    const calls = renderDashboard(
      serve(() => json(200, SUMMARY)),
      "/?jahr=2019",
    );
    expect(await screen.findByText(/Steuerjahr nicht unterstützt/)).toBeInTheDocument();
    expect(calls.some((c) => c.url.startsWith("/api/summary"))).toBe(false);
  });

  it("shows an error with retry on a server error", async () => {
    let fail = true;
    renderDashboard(serve(() => (fail ? json(500, { detail: "boom" }) : json(200, SUMMARY))));
    expect(await screen.findByText(/konnte nicht geladen werden/)).toBeInTheDocument();
    fail = false;
    screen.getByRole("button", { name: "Erneut versuchen" }).click();
    await waitFor(() =>
      expect(screen.getByText("Voraussichtliche Erstattung")).toBeInTheDocument(),
    );
  });
});

describe("no mock data", () => {
  it("has no import of the old mock module", () => {
    const walk = (dir: string): string[] =>
      readdirSync(dir).flatMap((f) => {
        const p = join(dir, f);
        return statSync(p).isDirectory() ? walk(p) : [p];
      });
    const needle = "lib/" + "mock";
    const hits = walk("src").filter((f) => {
      const text = readFileSync(f, "utf8");
      return text.includes(needle) && !f.endsWith("dashboard.test.tsx");
    });
    expect(hits).toEqual([]);
  });
});
