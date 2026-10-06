import { vi } from "vitest";

export interface Call {
  url: string;
  method: string;
  headers: Headers;
  credentials: RequestCredentials | undefined;
  body: unknown;
}

type Responder = (call: Call) => Response | Promise<Response>;

/** Replaces global `fetch`; records calls and answers via `respond` (default 404). */
export function mockFetch(respond: Responder) {
  const calls: Call[] = [];
  const fn = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const raw = init.body;
    const call: Call = {
      url,
      method: (init.method ?? "GET").toUpperCase(),
      headers: new Headers(init.headers),
      credentials: init.credentials,
      body: typeof raw === "string" ? JSON.parse(raw) : undefined,
    };
    calls.push(call);
    return respond(call);
  });
  vi.stubGlobal("fetch", fn);
  return { calls, fn };
}

export function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

export const ME = {
  user_id: "00000000-0000-4000-8000-000000000001",
  email: "owner@example.com",
  role: "owner",
  household_id: "00000000-0000-4000-8000-000000000002",
  household_name: "Musterhaushalt",
} as const;
