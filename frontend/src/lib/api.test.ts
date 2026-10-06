import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, apiFetch, loginHref, setUnauthorizedHandler } from "@/lib/api";
import { json, mockFetch } from "@/test/fetch-mock";

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  window.history.replaceState(null, "", "/");
});

describe("apiFetch", () => {
  it("calls /api with the CSRF header and same-origin credentials", async () => {
    const { calls } = mockFetch(() => json(200, { ok: true }));
    const result = await apiFetch("/auth/me");
    expect(result).toEqual({ ok: true });
    expect(calls[0]?.url).toBe("/api/auth/me");
    expect(calls[0]?.credentials).toBe("same-origin");
    expect(calls[0]?.headers.get("X-Requested-With")).toBe("belegbot");
  });

  it("sends JSON bodies", async () => {
    const { calls } = mockFetch(() => new Response(null, { status: 202 }));
    await apiFetch("/auth/magic-link", { method: "POST", json: { email: "a@example.com" } });
    expect(calls[0]?.method).toBe("POST");
    expect(calls[0]?.headers.get("Content-Type")).toBe("application/json");
    expect(calls[0]?.headers.get("X-Requested-With")).toBe("belegbot");
    expect(calls[0]?.body).toEqual({ email: "a@example.com" });
  });

  it("throws ApiError(status, detail)", async () => {
    mockFetch(() => json(429, { detail: "rate_limited" }));
    const error = await apiFetch("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 429, detail: "rate_limited" });
  });

  it("turns a 401 into a navigation to /login?next=<current path>", async () => {
    window.history.replaceState(null, "", "/belege?jahr=2025");
    mockFetch(() => json(401, { detail: "not_authenticated" }));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(apiFetch("/documents")).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledWith("/belege?jahr=2025");
    expect(loginHref("/belege")).toBe("/login?next=%2Fbelege");
  });

  it("default 401 handler assigns the login URL", async () => {
    window.history.replaceState(null, "", "/belege");
    const assign = vi.fn();
    const original = window.location;
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...original, pathname: "/belege", search: "", assign },
    });
    try {
      mockFetch(() => json(401, { detail: "not_authenticated" }));
      await expect(apiFetch("/documents")).rejects.toBeInstanceOf(ApiError);
      expect(assign).toHaveBeenCalledWith("/login?next=%2Fbelege");
    } finally {
      Object.defineProperty(window, "location", { configurable: true, value: original });
    }
  });

  it("can skip the 401 redirect", async () => {
    mockFetch(() => json(401, { detail: "not_authenticated" }));
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    await expect(apiFetch("/auth/me", { redirectOn401: false })).rejects.toBeInstanceOf(ApiError);
    expect(handler).not.toHaveBeenCalled();
  });
});
