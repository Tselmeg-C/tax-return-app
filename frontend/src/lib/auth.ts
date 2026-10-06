/**
 * The signed-in user (`GET /auth/me`) for the guard, the login page and the user menu.
 *
 * In the browser it goes through `apiFetch`; during SSR a server function calls the api
 * directly with the incoming request's `Cookie` header, so a hard reload of a protected page
 * without a session gets a server-side redirect.
 */
import { useQuery } from "@tanstack/react-query";
import { createServerFn } from "@tanstack/react-start";
import { getRequestHeader } from "@tanstack/react-start/server";

import { ApiError, apiFetch } from "@/lib/api";
import { buildUpstreamUrl, resolveApiUrl } from "@/server/api-proxy";

export const ME_KEY = ["auth", "me"] as const;
/** Seconds `me` counts as fresh; route changes after that re-check the session. */
export const ME_STALE_MS = 30_000;

export interface Me {
  user_id: string;
  email: string;
  role: "owner" | "member";
  household_id: string;
  household_name: string;
}

/** Server side: `${API_INTERNAL_URL}/auth/me` with the browser's cookie. `null` = 401. */
const fetchMeOnServer = createServerFn({ method: "GET" }).handler(async (): Promise<Me | null> => {
  const api = resolveApiUrl(process.env);
  if (api.kind !== "configured") throw new ApiError(503, "api not configured");
  const target = buildUpstreamUrl(api.url, new URL("http://web.internal/api/auth/me"));
  if (!target) throw new ApiError(500);
  const cookie = getRequestHeader("cookie");
  const response = await fetch(target, {
    headers: cookie ? { cookie } : {},
    redirect: "manual",
  });
  if (response.status === 401) return null;
  if (!response.ok) throw new ApiError(response.status);
  return (await response.json()) as Me;
});

async function fetchMeInBrowser(): Promise<Me | null> {
  try {
    return await apiFetch<Me>("/auth/me", { redirectOn401: false });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

/** The signed-in user, `null` without a valid session; throws on 5xx / network errors. */
export async function fetchMe(): Promise<Me | null> {
  if (typeof window === "undefined") return fetchMeOnServer();
  return fetchMeInBrowser();
}

export function useMe() {
  return useQuery({ queryKey: ME_KEY, queryFn: fetchMe, staleTime: ME_STALE_MS });
}

/**
 * The address and `next` of the last link request, kept in memory only (never in the URL or
 * browser storage) so `/login/sent` can resend.
 */
let pendingLogin: { email: string; next: string | undefined } | null = null;

export function rememberLoginRequest(email: string, next: string | undefined): void {
  pendingLogin = { email, next };
}

export function lastLoginRequest(): { email: string; next: string | undefined } | null {
  return pendingLogin;
}

export async function requestMagicLink(email: string, next: string | undefined): Promise<void> {
  await apiFetch("/auth/magic-link", {
    method: "POST",
    json: next ? { email, next } : { email },
    redirectOn401: false,
  });
}
