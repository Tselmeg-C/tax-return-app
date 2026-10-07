/**
 * The only way the browser talks to the api: same-origin `/api/*` through the web proxy.
 *
 * - `credentials: "same-origin"` (the httpOnly session cookie travels automatically)
 * - `X-Requested-With: belegbot` on every request (the api's CSRF check rejects state-changing
 *   requests without it)
 * - errors become `ApiError(status, detail)`; a `401` invalidates the `me` query and sends the
 *   user to `/login?next=<current path>` (opt out with `redirectOn401: false`)
 */

export const CSRF_HEADER = "X-Requested-With";
export const CSRF_VALUE = "belegbot";

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string | undefined;

  constructor(status: number, detail?: string) {
    super(`api error ${status}${detail ? ` (${detail})` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export interface ApiInit extends Omit<RequestInit, "body" | "credentials"> {
  /** JSON body (sets `Content-Type: application/json`). */
  json?: unknown;
  /** Raw body (e.g. a `File`), sent as is; set the `Content-Type` header yourself. */
  body?: BodyInit;
  /** Default true: a 401 calls the unauthorized handler (login redirect). */
  redirectOn401?: boolean;
}

export type UnauthorizedHandler = (next: string) => void;

export function loginHref(next: string): string {
  return `/login?next=${encodeURIComponent(next)}`;
}

export function currentPath(): string {
  if (typeof window === "undefined") return "/";
  return `${window.location.pathname}${window.location.search}`;
}

const defaultUnauthorized: UnauthorizedHandler = (next) => {
  if (typeof window !== "undefined") window.location.assign(loginHref(next));
};

let onUnauthorized: UnauthorizedHandler = defaultUnauthorized;

/** Set by the router (client only): invalidate `me` and navigate to the login page. */
export function setUnauthorizedHandler(handler: UnauthorizedHandler | null): void {
  onUnauthorized = handler ?? defaultUnauthorized;
}

async function readDetail(response: Response): Promise<string | undefined> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      const detail = (body as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : undefined;
    }
  } catch {
    // not JSON
  }
  return undefined;
}

export async function apiFetch<T = unknown>(path: string, init: ApiInit = {}): Promise<T> {
  const { json, body, redirectOn401 = true, headers: initHeaders, ...rest } = init;
  const headers = new Headers(initHeaders);
  headers.set(CSRF_HEADER, CSRF_VALUE);
  const request: RequestInit = { ...rest, headers, credentials: "same-origin" };
  if (json !== undefined) {
    headers.set("Content-Type", "application/json");
    request.body = JSON.stringify(json);
  } else if (body !== undefined) {
    request.body = body;
  }

  const response = await fetch(`/api${path}`, request);
  if (!response.ok) {
    const detail = await readDetail(response);
    if (response.status === 401 && redirectOn401) onUnauthorized(currentPath());
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  return (text ? JSON.parse(text) : undefined) as T;
}
