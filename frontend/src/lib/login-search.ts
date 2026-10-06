/** Search params of the login pages: an optional `next` path to return to after login. */
export interface LoginSearch {
  next?: string;
}

export function validateLoginSearch(search: Record<string, unknown>): LoginSearch {
  const next = search["next"];
  // The api re-checks `next` (same-site path only); this just drops non-strings.
  return typeof next === "string" && next.startsWith("/") ? { next } : {};
}
