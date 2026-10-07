import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DocumentUploads } from "@/components/documents/DocumentUploads";
import { MESSAGES, uploadDocument, type DocumentOut } from "@/lib/documents";
import { json, mockFetch, type Call } from "@/test/fetch-mock";

let seq = 0;
function doc(patch: Partial<DocumentOut> = {}): DocumentOut {
  seq += 1;
  return {
    id: `00000000-0000-4000-8000-${String(seq).padStart(12, "0")}`,
    status: "queued",
    original_filename: null,
    mime_type: "application/pdf",
    size_bytes: 10,
    channel: "web",
    doc_type: null,
    error_kind: null,
    created_at: "2026-10-05T10:00:00Z",
    updated_at: "2026-10-05T10:00:00Z",
    ...patch,
  };
}

function file(name = "beleg.pdf", size?: number): File {
  const f = new File(["%PDF-1.7 test"], name, { type: "application/pdf" });
  if (size !== undefined) Object.defineProperty(f, "size", { value: size });
  return f;
}

function renderUploads() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={queryClient}>
      <DocumentUploads />
    </QueryClientProvider>,
  );
  return { queryClient, view };
}

const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");
const fileInput = () => screen.getByTestId("file-input") as HTMLInputElement;
const pick = (...files: File[]) => fireEvent.change(fileInput(), { target: { files } });

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("uploadDocument", () => {
  it("sends the File as raw body with the right headers", async () => {
    const { calls } = mockFetch(() => json(201, { document: doc(), duplicate: false }));
    const f = file("Müller Rechnung.pdf");
    await uploadDocument(f);
    expect(calls).toHaveLength(1);
    const call = calls[0]!;
    expect(call.url).toBe("/api/documents");
    expect(call.method).toBe("POST");
    expect(call.rawBody).toBe(f);
    expect(call.headers.get("Content-Type")).toBe("application/octet-stream");
    expect(call.headers.get("X-Requested-With")).toBe("belegbot");
    expect(call.headers.get("X-Filename")).toBe("UTF-8''M%C3%BCller%20Rechnung.pdf");
  });
});

describe("upload area", () => {
  it("has the right accept and capture attributes", () => {
    mockFetch(() => json(200, { documents: [] }));
    renderUploads();
    expect(fileInput().getAttribute("accept")).toBe("image/*,application/pdf");
    expect(fileInput().multiple).toBe(true);
    const camera = screen.getByTestId("camera-input");
    expect(camera.getAttribute("accept")).toBe("image/*");
    expect(camera.getAttribute("capture")).toBe("environment");
  });

  it("runs at most 3 uploads at once and gives every file its own row", async () => {
    const resolvers: (() => void)[] = [];
    let inFlight = 0;
    let maxInFlight = 0;
    mockFetch((call) => {
      if (call.method !== "POST") return json(200, { documents: [] });
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise<Response>((resolve) =>
        resolvers.push(() => {
          inFlight -= 1;
          resolve(json(201, { document: doc({ original_filename: "x.pdf" }), duplicate: false }));
        }),
      );
    });
    renderUploads();
    pick(...[1, 2, 3, 4, 5].map((n) => file(`f${n}.pdf`)));
    expect(screen.getAllByText("wird hochgeladen…")).toHaveLength(5);
    await waitFor(() => expect(resolvers).toHaveLength(3));
    expect(maxInFlight).toBe(3);
    while (resolvers.length) {
      await act(async () => resolvers.shift()!());
      await new Promise((r) => setTimeout(r, 0));
    }
    await waitFor(() => expect(screen.queryAllByText("wird hochgeladen…")).toHaveLength(0));
    expect(maxInFlight).toBe(3);
  });

  it("pre-checks too large and empty files without a request", async () => {
    const { calls } = mockFetch(() => json(200, { documents: [] }));
    renderUploads();
    pick(file("gross.pdf", 26 * 1024 * 1024), file("leer.pdf", 0));
    expect(await screen.findByText(MESSAGES.tooLarge)).toBeInTheDocument();
    expect(screen.getByText(MESSAGES.empty)).toBeInTheDocument();
    expect(screen.getAllByText("Erneut hochladen")).toHaveLength(2);
    expect(posts(calls)).toHaveLength(0);
  });

  it("shows 'Bereits vorhanden' for a duplicate", async () => {
    const existing = doc({ status: "done", original_filename: "alt.pdf" });
    mockFetch((call) =>
      call.method === "POST"
        ? json(200, { document: existing, duplicate: true, requeued: false })
        : json(200, { documents: [existing] }),
    );
    renderUploads();
    pick(file());
    expect(await screen.findByText(MESSAGES.duplicate)).toBeInTheDocument();
  });

  const cases: [string, () => Response | Promise<Response>, string, "resend" | "pick"][] = [
    ["network", () => Promise.reject(new TypeError("Failed to fetch")), MESSAGES.failed, "resend"],
    ["413", () => json(413, { detail: "too_large" }), MESSAGES.tooLarge, "pick"],
    ["415", () => json(415, { detail: "unsupported_type" }), MESSAGES.unsupported, "pick"],
    ["422", () => json(422, { detail: "empty_file" }), MESSAGES.empty, "pick"],
    ["500", () => json(500, {}), MESSAGES.failed, "resend"],
    ["502", () => json(502, {}), MESSAGES.failed, "resend"],
    ["503", () => json(503, {}), MESSAGES.failed, "resend"],
    ["504", () => json(504, {}), MESSAGES.failed, "resend"],
    ["507", () => json(507, { detail: "storage_full" }), MESSAGES.storageFull, "resend"],
  ];

  it.each(cases)("%s shows its message and 'Erneut hochladen'", async (_, fail, text, mode) => {
    const { calls } = mockFetch((call) =>
      call.method === "POST" ? fail() : json(200, { documents: [] }),
    );
    renderUploads();
    const clicked = vi.spyOn(fileInput(), "click");
    const f = file();
    pick(f);
    expect(await screen.findByText(text)).toBeInTheDocument();
    fireEvent.click(screen.getByText("Erneut hochladen"));
    if (mode === "resend") {
      await waitFor(() => expect(posts(calls)).toHaveLength(2));
      expect(posts(calls)[1]!.rawBody).toBe(f);
      expect(clicked).not.toHaveBeenCalled();
    } else {
      expect(clicked).toHaveBeenCalledTimes(1);
      expect(posts(calls)).toHaveLength(1);
    }
  });
});

describe("Hochgeladene Belege", () => {
  it("retries a failed document with the held File, else opens the picker", async () => {
    const created = doc({ original_filename: "beleg.pdf" });
    let listed: DocumentOut[] = [];
    let postResponse = () => {
      listed = [created];
      return json(201, { document: created, duplicate: false });
    };
    const { calls } = mockFetch((call) =>
      call.method === "POST" ? postResponse() : json(200, { documents: listed }),
    );
    const { queryClient, view } = renderUploads();
    const f = file();
    pick(f);
    await screen.findByText("läuft…");

    listed = [{ ...created, status: "failed", error_kind: "ValueError" }];
    await act(() => queryClient.invalidateQueries());
    expect(await screen.findByText(MESSAGES.processingFailed)).toBeInTheDocument();

    postResponse = () =>
      json(200, { document: { ...created, status: "queued" }, duplicate: true, requeued: true });
    listed = [{ ...created, status: "queued" }];
    fireEvent.click(screen.getByText("Erneut hochladen"));
    expect(await screen.findByText(MESSAGES.requeued)).toBeInTheDocument();
    expect(screen.getByText("läuft…")).toBeInTheDocument();
    expect(posts(calls)[1]!.rawBody).toBe(f);

    // After a reload the tab no longer holds the File: the button opens the picker.
    view.unmount();
    listed = [{ ...created, status: "failed" }];
    renderUploads();
    await screen.findByText(MESSAGES.processingFailed);
    const clicked = vi.spyOn(fileInput(), "click");
    fireEvent.click(screen.getByText("Erneut hochladen"));
    expect(clicked).toHaveBeenCalledTimes(1);
    expect(posts(calls)).toHaveLength(2);
  });

  it("polls every 2 s while a document runs and stops afterwards", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let status: DocumentOut["status"] = "processing";
    const one = doc();
    const { calls } = mockFetch(() => json(200, { documents: [{ ...one, status }] }));
    renderUploads();
    await screen.findByText("läuft…");
    const gets = () => calls.filter((c) => c.method === "GET").length;
    const before = gets();
    await act(() => vi.advanceTimersByTimeAsync(2100));
    await waitFor(() => expect(gets()).toBe(before + 1));
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await waitFor(() => expect(gets()).toBe(before + 2));

    status = "done";
    await act(() => vi.advanceTimersByTimeAsync(2000));
    await screen.findByText("verarbeitet");
    const settled = gets();
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(gets()).toBe(settled);
  });

  it("shows file names as text, or 'Beleg vom …' without one", async () => {
    const evil = "<img src=x onerror=alert(1)>.jpg";
    mockFetch(() =>
      json(200, {
        documents: [
          doc({ original_filename: "Rechnung Müller.pdf", status: "done" }),
          doc({ original_filename: null, status: "done" }),
          doc({ original_filename: evil, status: "done" }),
        ],
      }),
    );
    const { view } = renderUploads();
    expect(await screen.findByText("Rechnung Müller.pdf")).toBeInTheDocument();
    expect(screen.getByText(/^Beleg vom /)).toBeInTheDocument();
    expect(screen.getByText(evil)).toBeInTheDocument();
    expect(view.container.querySelector("img")).toBeNull();
    const links = screen.getAllByText("Original öffnen");
    expect(links[0]!.closest("a")?.getAttribute("href")).toMatch(/^\/api\/documents\/.+\/file$/);
    expect(links[0]!.closest("a")?.getAttribute("target")).toBe("_blank");
  });

  it("deletes after confirmation; a 409 keeps the row", async () => {
    const keep = doc({ original_filename: "bleibt.pdf", status: "processing" });
    const gone = doc({ original_filename: "weg.pdf", status: "done" });
    const { calls } = mockFetch((call) => {
      if (call.method === "DELETE")
        return call.url.endsWith(keep.id)
          ? json(409, { detail: "document_busy" })
          : new Response(null, { status: 204 });
      return json(200, { documents: call.method === "GET" && deleted ? [keep] : [keep, gone] });
    });
    let deleted = false;
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderUploads();
    const goneRow = (await screen.findByText("weg.pdf")).closest("li")!;
    deleted = true;
    fireEvent.click(within(goneRow).getByText("Löschen"));
    await waitFor(() => expect(screen.queryByText("weg.pdf")).toBeNull());
    expect(confirm).toHaveBeenCalled();
    expect(calls.some((c) => c.method === "DELETE" && c.url === `/api/documents/${gone.id}`)).toBe(
      true,
    );

    const keepRow = screen.getByText("bleibt.pdf").closest("li")!;
    fireEvent.click(within(keepRow).getByText("Löschen"));
    expect(await screen.findByText(MESSAGES.busy)).toBeInTheDocument();
    expect(screen.getByText("bleibt.pdf")).toBeInTheDocument();
  });
});
