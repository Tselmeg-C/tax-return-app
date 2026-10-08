import { readFileSync } from "node:fs";

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

import { HouseholdPage } from "@/components/household/HouseholdPage";
import { Toaster } from "@/components/ui/sonner";
import {
  TEXT,
  type ChildYearOut,
  type EmploymentOut,
  type HouseholdLabels,
  type HouseholdOut,
  type PersonOut,
  type ProfileOut,
} from "@/lib/household";
import { belegeSearch } from "@/lib/taxItems";
import { json, ME, mockFetch, type Call } from "@/test/fetch-mock";

const coded = (pairs: [string, string][]) => pairs.map(([code, label]) => ({ code, label }));
const LABELS: HouseholdLabels = {
  categories: [],
  anlagen: [],
  attention_reasons: [],
  payment_methods: [],
  doc_types: [],
  supported_years: [2025, 2026],
  bundeslaender: coded([
    ["be", "Berlin"],
    ["by", "Bayern"],
  ]),
  filing_statuses: coded([
    ["single", "Einzelveranlagung (Grundtarif)"],
    ["joint", "Zusammenveranlagung (Splittingtarif)"],
  ]),
  steuerklassen: coded([
    ["1", "I"],
    ["3", "III"],
    ["4", "IV"],
    ["6", "VI"],
  ]),
  religions: coded([
    ["none", "Keine / nicht kirchensteuerpflichtig"],
    ["ev", "Evangelisch"],
  ]),
  allowance_shares: coded([
    ["full", "Voller Freibetrag (beide Elternteile)"],
    ["half", "Halber Freibetrag"],
  ]),
  person_kinds: coded([
    ["adult", "Erwachsene Person"],
    ["child", "Kind"],
  ]),
};

/** A typed Steuer-ID made at runtime; the leading 0 makes it invalid for any real person. */
const sentinelId = () => `0${String(Math.random()).slice(2, 12).padEnd(10, "7")}`;

let seq = 0;
const nextId = () => `00000000-0000-4000-8000-${String((seq += 1)).padStart(12, "0")}`;

function person(patch: Partial<PersonOut> = {}): PersonOut {
  return {
    id: nextId(),
    kind: "adult",
    first_name: "Alex",
    last_name: "Muster",
    dob: "1985-04-02",
    religion: "none",
    disability_grade: null,
    steuer_id_masked: null,
    is_me: false,
    ...patch,
  };
}

/** In-memory household api: answers like the backend and records every call. */
class FakeApi {
  persons: PersonOut[] = [];
  profiles = new Map<number, ProfileOut>();
  employments: EmploymentOut[] = [];
  childYears: ChildYearOut[] = [];
  maxMonths = 12;
  /** Overrides for single calls (return undefined to fall through). */
  other: (call: Call) => Response | Promise<Response> | undefined = () => undefined;

  household(year: number): HouseholdOut {
    const profile = this.profiles.get(year) ?? null;
    const inReturn = [profile?.taxpayer_person_id, profile?.spouse_person_id];
    return {
      year,
      profile,
      persons: this.persons,
      employments: this.employments
        .filter((e) => e.year === year)
        .map((e) => ({ ...e, in_return: inReturn.includes(e.person_id) })),
      children: this.persons
        .filter((p) => p.kind === "child" && Number(p.dob!.slice(0, 4)) <= year)
        .map((p) => ({
          person_id: p.id,
          max_months: this.maxMonths,
          child_year: this.childYears.find((c) => c.person_id === p.id && c.year === year) ?? null,
        })),
      years_with_profile: [...this.profiles.keys()].sort(),
    };
  }

  serve() {
    return mockFetch(async (call) => {
      const extra = await this.other(call);
      if (extra) return extra;
      const path = call.url.split("?")[0]!;
      const body = call.body as Record<string, unknown>;
      if (path === "/api/auth/me") return json(200, ME);
      if (path === "/api/meta/labels") return json(200, LABELS);
      let m: RegExpMatchArray | null;
      if (path === "/api/persons" && call.method === "POST") {
        const { steuer_id, link_to_me, ...rest } = body;
        const p = person({
          ...(rest as Partial<PersonOut>),
          is_me: Boolean(link_to_me),
          steuer_id_masked: steuer_id ? `XX XXX XXX ${String(steuer_id).slice(-3)}` : null,
        });
        this.persons.push(p);
        return json(201, p);
      }
      if ((m = path.match(/^\/api\/persons\/(.+)$/)) && call.method === "PATCH") {
        const p = this.persons.find((x) => x.id === m![1])!;
        const { steuer_id, ...rest } = body;
        Object.assign(p, rest);
        if (steuer_id !== undefined) {
          p.steuer_id_masked = steuer_id ? `XX XXX XXX ${String(steuer_id).slice(-3)}` : null;
        }
        return json(200, p);
      }
      if ((m = path.match(/^\/api\/persons\/(.+)$/)) && call.method === "DELETE") {
        this.persons = this.persons.filter((x) => x.id !== m![1]);
        return new Response(null, { status: 204 });
      }
      if ((m = path.match(/^\/api\/household\/(\d+)$/))) {
        return json(200, this.household(Number(m[1])));
      }
      if ((m = path.match(/^\/api\/household\/(\d+)\/profile$/))) {
        const profile = { year: Number(m[1]), ...body } as unknown as ProfileOut;
        this.profiles.set(profile.year, profile);
        return json(200, profile);
      }
      if ((m = path.match(/^\/api\/household\/(\d+)\/copy$/))) {
        const year = Number(m[1]);
        const from = this.profiles.get(body["from_year"] as number)!;
        this.profiles.set(year, { ...from, year });
        return json(200, this.household(year));
      }
      if ((m = path.match(/^\/api\/household\/(\d+)\/children\/(.+)$/))) {
        const row = { person_id: m[2]!, year: Number(m[1]), ...body } as unknown as ChildYearOut;
        this.childYears.push(row);
        return json(200, row);
      }
      if (path === "/api/employments" && call.method === "POST") {
        const e = { id: nextId(), in_return: true, ...body } as unknown as EmploymentOut;
        this.employments.push(e);
        return json(201, e);
      }
      return json(404, { detail: "not_found" });
    });
  }
}

function renderPage(path: string) {
  const root = createRootRoute({
    component: () => (
      <>
        <Outlet />
        <Toaster />
      </>
    ),
  });
  const page = (p: string, component = () => <p>{p}</p>) =>
    createRoute({ getParentRoute: () => root, path: p, validateSearch: belegeSearch, component });
  const router = createRouter({
    routeTree: root.addChildren([
      page("/haushalt", HouseholdPage),
      page("/"),
      page("/belege"),
      page("/export"),
    ]),
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

const householdGets = (calls: Call[]) =>
  calls.filter((c) => c.method === "GET" && c.url.startsWith("/api/household"));
const writes = (calls: Call[]) => calls.filter((c) => c.method !== "GET");

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("Haushalt year handling", () => {
  it("has no mock data import", () => {
    const src = readFileSync("src/routes/_authed/haushalt.tsx", "utf8");
    const page = readFileSync("src/components/household/HouseholdPage.tsx", "utf8");
    expect(src + page).not.toContain("@/lib/mock");
  });

  it("keeps ?jahr= in nav links and refetches on a year change", async () => {
    const api = new FakeApi();
    const { calls } = api.serve();
    renderPage("/haushalt?jahr=2026");
    expect(await screen.findByText("Du")).toBeInTheDocument();
    for (const href of ["/?jahr=2026", "/belege?jahr=2026", "/haushalt?jahr=2026"]) {
      expect(document.querySelector(`a[href="${href}"]`)).not.toBeNull();
    }
    expect(document.querySelector('a[href="/export?jahr=2026"]')).not.toBeNull();
    expect(householdGets(calls).map((c) => c.url)).toEqual(["/api/household/2026"]);
    fireEvent.change(screen.getByLabelText("Steuerjahr"), { target: { value: "2025" } });
    await waitFor(() =>
      expect(householdGets(calls).map((c) => c.url)).toContain("/api/household/2025"),
    );
  });

  it("an unsupported year shows its text and sends no household request", async () => {
    const { calls } = new FakeApi().serve();
    renderPage("/haushalt?jahr=2024");
    expect(
      await screen.findByText("Steuerjahr 2024 wird nicht unterstützt (nur 2025, 2026)"),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Steuerjahr")).toBeInTheDocument();
    expect(householdGets(calls)).toHaveLength(0);
  });

  it("shows loading and error states; Erneut versuchen refetches", async () => {
    const api = new FakeApi();
    let fail = true;
    api.other = (call) =>
      call.url.startsWith("/api/household") && fail ? json(500, {}) : undefined;
    const { calls } = api.serve();
    renderPage("/haushalt?jahr=2025");
    expect(await screen.findByText(TEXT.loading)).toBeInTheDocument();
    expect(await screen.findByText(TEXT.loadError)).toBeInTheDocument();
    fail = false;
    fireEvent.click(screen.getByText(TEXT.retry));
    expect(await screen.findByText("Schritt 1 von 4")).toBeInTheDocument();
    expect(householdGets(calls)).toHaveLength(2);
  });
});

describe("Haushalt wizard", () => {
  it("runs Du → Veranlagung → Arbeit → Kinder, saving each step through apiFetch", async () => {
    const api = new FakeApi();
    const kid = person({ kind: "child", first_name: "Kim", dob: "2025-03-10" });
    api.maxMonths = 10;
    let failSteuerId = true;
    api.other = (call) =>
      call.url === "/api/persons" && call.method === "POST" && failSteuerId
        ? json(422, { detail: "invalid_steuer_id", field: "steuer_id" })
        : undefined;
    const { calls } = api.serve();
    const { queryClient, router } = renderPage("/haushalt?jahr=2025");

    // step 1: Du, a 422 keeps the step and the input
    expect(await screen.findByText("Schritt 1 von 4")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Vorname"), { target: { value: "Alex" } });
    fireEvent.change(screen.getByLabelText("Geburtsdatum"), { target: { value: "1985-04-02" } });
    const steuerInput = screen.getByLabelText("Steuer-ID (optional)");
    expect(steuerInput).toHaveAttribute("inputmode", "numeric");
    expect(steuerInput).toHaveAttribute("autocomplete", "off");
    fireEvent.change(steuerInput, { target: { value: "123" } });
    fireEvent.click(screen.getByText("Weiter"));
    expect(await screen.findByText(TEXT.steuerIdDigits)).toBeInTheDocument();
    expect(writes(calls)).toHaveLength(0);

    const typed = sentinelId();
    fireEvent.change(steuerInput, { target: { value: typed } });
    fireEvent.click(screen.getByText("Weiter"));
    expect(await screen.findByText(TEXT.invalidSteuerId)).toBeInTheDocument();
    expect(screen.getByText("Schritt 1 von 4")).toBeInTheDocument();
    expect(screen.getByLabelText("Vorname")).toHaveValue("Alex");

    failSteuerId = false;
    fireEvent.click(screen.getByText("Weiter"));
    expect(await screen.findByText("Schritt 2 von 4")).toBeInTheDocument();
    const post = writes(calls).at(-1)!;
    expect(post.url).toBe("/api/persons");
    expect(post.headers.get("X-Requested-With")).toBe("belegbot");
    expect(post.body).toMatchObject({ kind: "adult", first_name: "Alex", link_to_me: true });
    expect(post.body).toMatchObject({ steuer_id: typed });

    // step 2: Zusammen with a new partner → partner POST, then PUT profile
    fireEvent.change(screen.getByLabelText(/Bundesland/), { target: { value: "be" } });
    fireEvent.click(screen.getByLabelText("Zusammen"));
    fireEvent.change(screen.getByLabelText("Vorname"), { target: { value: "Robin" } });
    fireEvent.click(screen.getByText("Weiter"));
    expect(await screen.findByText("Schritt 3 von 4")).toBeInTheDocument();
    const [partner, profile] = writes(calls).slice(-2);
    expect(partner!.url).toBe("/api/persons");
    expect(partner!.body).toMatchObject({ kind: "adult", first_name: "Robin" });
    expect(partner!.body).not.toHaveProperty("link_to_me");
    expect(profile!.method).toBe("PUT");
    expect(profile!.url).toBe("/api/household/2025/profile");
    expect(profile!.body).toMatchObject({ filing_status: "joint", bundesland: "be" });

    // step 3: one POST per employer; the wizard stays open after the first one
    const addJob = async (employer: string, klasse: string) => {
      fireEvent.click(screen.getAllByText("Arbeitgeber hinzufügen")[0]!);
      fireEvent.change(screen.getByLabelText("Arbeitgeber"), { target: { value: employer } });
      fireEvent.change(screen.getByLabelText("Steuerklasse"), { target: { value: klasse } });
      fireEvent.click(screen.getByText("Speichern"));
      expect(await screen.findByText(new RegExp(employer))).toBeInTheDocument();
    };
    await addJob("Muster GmbH", "1");
    await addJob("Beispiel AG", "6");
    expect(screen.getByText("Schritt 3 von 4")).toBeInTheDocument();
    const jobs = writes(calls).filter((c) => c.url === "/api/employments");
    expect(jobs.map((c) => c.body)).toMatchObject([
      { employer_name: "Muster GmbH", steuerklasse: "1", year: 2025 },
      { employer_name: "Beispiel AG", steuerklasse: "6", year: 2025 },
    ]);
    fireEvent.click(screen.getByText("Weiter"));

    // step 4: a child row defaults to max_months
    expect(await screen.findByText("Schritt 4 von 4")).toBeInTheDocument();
    api.persons.push(kid);
    await queryClient.invalidateQueries();
    const card = (await screen.findByText(/Kim/)).closest(".sheet") as HTMLElement;
    expect(within(card).getByLabelText("Monate")).toHaveValue(10);
    fireEvent.click(within(card).getByText("Speichern"));
    await waitFor(() =>
      expect(writes(calls).at(-1)!.url).toBe(`/api/household/2025/children/${kid.id}`),
    );
    expect(writes(calls).at(-1)!.body).toEqual({
      months: 10,
      allowance_share: "full",
      in_household: true,
    });
    fireEvent.click(screen.getByText("Fertig"));
    expect(await screen.findByText("Veranlagung 2025")).toBeInTheDocument();

    // the typed ID is in no URL and in no cached query data
    const cached = JSON.stringify(
      queryClient
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    );
    expect(cached).not.toContain(typed);
    expect(router.state.location.href).not.toContain(typed);
    expect(calls.filter((c) => c.url.includes(typed))).toHaveLength(0);
    expect(
      screen.getByText(new RegExp(`Steuer-ID XX XXX XXX ${typed.slice(-3)}`)),
    ).toBeInTheDocument();
  });

  it("resumes at step 3 after a reload when the profile is saved", async () => {
    const api = new FakeApi();
    const me = person({ is_me: true });
    api.persons = [me];
    api.profiles.set(2025, {
      year: 2025,
      filing_status: "single",
      bundesland: "be",
      taxpayer_person_id: me.id,
      spouse_person_id: null,
    });
    api.serve();
    renderPage("/haushalt?jahr=2025");
    expect(await screen.findByText("Schritt 3 von 4")).toBeInTheDocument();
    expect(screen.getByText("Keine Anstellung in 2025")).toBeInTheDocument();
  });

  it("offers Aus {Jahr} übernehmen only with another year's profile", async () => {
    const api = new FakeApi();
    const me = person({ is_me: true });
    api.persons = [me];
    api.employments = [
      {
        id: nextId(),
        person_id: me.id,
        year: 2025,
        employer_name: "Muster GmbH",
        steuerklasse: "1",
        has_factor: false,
        commute_km: 12,
        office_days: 100,
        homeoffice_days: 50,
        in_return: true,
      },
    ];
    api.profiles.set(2025, {
      year: 2025,
      filing_status: "single",
      bundesland: "be",
      taxpayer_person_id: me.id,
      spouse_person_id: null,
    });
    api.other = (call) => {
      if (call.url === "/api/household/2026/copy") {
        api.employments.push({ ...api.employments[0]!, id: nextId(), year: 2026 });
      }
      return undefined;
    };
    const { calls } = api.serve();
    renderPage("/haushalt?jahr=2026");
    fireEvent.click(await screen.findByText("Aus 2025 übernehmen"));
    expect(await screen.findByText("Haushalt aus 2025 übernommen – bitte prüfen")).toBeVisible();
    expect(await screen.findByText("Veranlagung 2026")).toBeInTheDocument();
    const copy = writes(calls).find((c) => c.url === "/api/household/2026/copy")!;
    expect(copy.body).toEqual({ from_year: 2025 });
    expect(copy.headers.get("X-Requested-With")).toBe("belegbot");
  });

  it("does not offer copy without another year", async () => {
    new FakeApi().serve();
    renderPage("/haushalt?jahr=2025");
    expect(await screen.findByText("Schritt 1 von 4")).toBeInTheDocument();
    expect(screen.queryByText(/übernehmen/)).toBeNull();
  });
});

describe("Haushalt page view", () => {
  function joinedHousehold() {
    const api = new FakeApi();
    const me = person({ is_me: true, first_name: "Alex", religion: "ev" });
    const partner = person({ first_name: "Robin", steuer_id_masked: "XX XXX XXX 123" });
    const kid = person({ kind: "child", first_name: "Kim", dob: "2007-05-01" });
    api.persons = [me, partner, kid];
    api.profiles.set(2025, {
      year: 2025,
      filing_status: "joint",
      bundesland: "by",
      taxpayer_person_id: me.id,
      spouse_person_id: partner.id,
    });
    api.employments = [
      {
        id: nextId(),
        person_id: partner.id,
        year: 2025,
        employer_name: "Beispiel AG",
        steuerklasse: "4",
        has_factor: true,
        commute_km: null,
        office_days: 0,
        homeoffice_days: 0,
        in_return: true,
      },
    ];
    api.childYears = [
      { person_id: kid.id, year: 2025, months: 12, allowance_share: "full", in_household: true },
    ];
    return { api, me, partner, kid };
  }

  it("shows profile, masked Steuer-ID, church and 18+ hints", async () => {
    const { api } = joinedHousehold();
    api.serve();
    renderPage("/haushalt?jahr=2025");
    expect(await screen.findByText("Veranlagung 2025")).toBeInTheDocument();
    expect(screen.getByText("Zusammenveranlagung (Splittingtarif)")).toBeInTheDocument();
    expect(screen.getByText("Bayern")).toBeInTheDocument();
    expect(screen.getByText("Alex: ja, Robin: nein")).toBeInTheDocument();
    expect(screen.getByText(TEXT.churchHint)).toBeInTheDocument();
    expect(screen.getByText(/Steuer-ID XX XXX XXX 123/)).toBeInTheDocument();
    expect(screen.getByText(TEXT.adultChild)).toBeInTheDocument();
  });

  it("joint → single greys the spouse with her employment and shows the children hint", async () => {
    const { api } = joinedHousehold();
    api.serve();
    renderPage("/haushalt?jahr=2025");
    const card = (await screen.findByText("Veranlagung 2025")).closest("section")!;
    fireEvent.click(within(card).getByText("Bearbeiten"));
    fireEvent.click(within(card).getByLabelText("Einzeln"));
    fireEvent.click(within(card).getByText("Speichern"));
    expect(await screen.findByText(TEXT.childrenReview)).toBeInTheDocument();
    expect(await screen.findAllByText("Nicht Teil der Erklärung 2025")).not.toHaveLength(0);
    const job = screen.getByText(/Beispiel AG/).closest("li")!;
    expect(job.className).toContain("opacity-60");
    expect(within(job).getByText("Nicht Teil der Erklärung 2025")).toBeInTheDocument();
    expect(within(job).getByText("Löschen")).toBeInTheDocument();
  });

  it.each([
    [
      { detail: "person_has_tax_items", count: 3 },
      "Robin Muster ist noch 3 Belegen zugeordnet. Ordne diese Belege zuerst einer anderen Person oder dem Haushalt zu (Seite Belege).",
    ],
    [
      { detail: "person_in_profile", years: [2025] },
      "Robin Muster ist in der Veranlagung 2025 eingetragen. Ändere zuerst die Veranlagung.",
    ],
  ])("delete 409 %o shows its text", async (body, text) => {
    const { api, partner } = joinedHousehold();
    api.other = (call) => (call.method === "DELETE" ? json(409, body) : undefined);
    api.serve();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage("/haushalt?jahr=2025");
    const card = (await screen.findAllByText("Robin Muster"))[0]!.closest(".sheet") as HTMLElement;
    fireEvent.click(within(card).getAllByText("Löschen")[0]!);
    expect(await within(card).findByText(text)).toBeInTheDocument();
    expect(confirm).toHaveBeenCalledWith(
      "Robin Muster löschen? Arbeitsverhältnisse und Kinderangaben dieser Person werden für alle Jahre entfernt. Belege bleiben erhalten.",
    );
    expect(api.persons.some((p) => p.id === partner.id)).toBe(true);
  });

  it("a saved Steuer-ID clears the input and shows the masked value", async () => {
    const { api } = joinedHousehold();
    const { calls } = api.serve();
    const { queryClient } = renderPage("/haushalt?jahr=2025");
    const card = (await screen.findAllByText("Alex Muster"))[0]!.closest(".sheet") as HTMLElement;
    fireEvent.click(within(card).getAllByText("Bearbeiten")[0]!);
    const typed = sentinelId();
    fireEvent.change(within(card).getByLabelText("Steuer-ID (optional)"), {
      target: { value: typed },
    });
    fireEvent.click(within(card).getByText("Speichern"));
    expect(
      await within(card).findByText(new RegExp(`XX XXX XXX ${typed.slice(-3)}`)),
    ).toBeInTheDocument();
    const patch = writes(calls).find((c) => c.method === "PATCH")!;
    expect(patch.body).toMatchObject({ steuer_id: typed });
    expect(within(card).queryByDisplayValue(typed)).toBeNull();
    const cached = JSON.stringify(
      queryClient
        .getQueryCache()
        .getAll()
        .map((q) => q.state.data),
    );
    expect(cached).not.toContain(typed);
  });
});
