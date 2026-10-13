import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TelegramCard, TELEGRAM_TEXT } from "@/components/household/TelegramCard";
import { json, mockFetch } from "@/test/fetch-mock";

afterEach(() => vi.unstubAllGlobals());

function renderCard() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <TelegramCard />
    </QueryClientProvider>,
  );
  return queryClient;
}

describe("TelegramCard (#11)", () => {
  it("shows the one-time code with a countdown and keeps it out of the query cache", async () => {
    const code = "48291037";
    const { calls } = mockFetch((c) => {
      if (c.url === "/api/bot/link") return json(200, { linked: false, channel: "telegram" });
      if (c.url === "/api/bot/link-code" && c.method === "POST")
        return json(200, { code, expires_at: new Date(Date.now() + 600_000).toISOString() });
      return json(404, { detail: "not_found" });
    });
    const queryClient = renderCard();
    const create = (await screen.findByRole("button", {
      name: TELEGRAM_TEXT.create,
    })) as HTMLButtonElement;
    await waitFor(() => expect(create.disabled).toBe(false)); // the link state has loaded
    fireEvent.click(create);
    expect((await screen.findByTestId("telegram-code")).textContent).toBe(code);
    expect(screen.getByText(/Gültig noch/)).toBeTruthy();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.headers.get("X-Requested-With")).toBe("belegbot");
    expect(
      JSON.stringify(
        queryClient
          .getQueryCache()
          .getAll()
          .map((q) => q.state.data),
      ),
    ).not.toContain(code);
    expect(queryClient.getMutationCache().getAll()).toHaveLength(0);
  });

  it("shows the linked state and unlinks", async () => {
    let linked = true;
    const { calls } = mockFetch((c) => {
      if (c.url === "/api/bot/link" && c.method === "DELETE") {
        linked = false;
        return new Response(null, { status: 204 });
      }
      if (c.url === "/api/bot/link") return json(200, { linked, channel: "telegram" });
      return json(404, { detail: "not_found" });
    });
    renderCard();
    expect(await screen.findByText(TELEGRAM_TEXT.linked)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: TELEGRAM_TEXT.unlink }));
    expect(await screen.findByRole("button", { name: TELEGRAM_TEXT.create })).toBeTruthy();
    expect(calls.some((c) => c.method === "DELETE")).toBe(true);
  });

  it("says so when the status cannot be loaded", async () => {
    mockFetch(() => json(500, { detail: "boom" }));
    renderCard();
    await waitFor(() => expect(screen.getByText(TELEGRAM_TEXT.unavailable)).toBeTruthy());
  });
});
