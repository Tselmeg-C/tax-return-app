/** Texts and helpers of the login pages (kept out of the components for fast refresh). */
import { ApiError } from "@/lib/api";

export const INVALID_EMAIL_TEXT = "Bitte eine gültige E-Mail-Adresse eingeben";
export const RATE_LIMITED_TEXT = "Zu viele Versuche, bitte später erneut versuchen";
export const GENERIC_ERROR_TEXT = "Das hat nicht geklappt, bitte erneut versuchen";
export const INVALID_LINK_TEXT = "Dieser Link ist ungültig, abgelaufen oder wurde schon benutzt";

export function errorText(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) return INVALID_EMAIL_TEXT;
  if (error instanceof ApiError && error.status === 429) return RATE_LIMITED_TEXT;
  return GENERIC_ERROR_TEXT;
}

/**
 * Reads `#token=…` from the fragment and removes it from the address bar before anything
 * else. Nothing is sent until "Anmelden" is clicked, so mail scanners that open the link do
 * not use it up.
 */
export function readAndStripToken(): string | null {
  const hash = window.location.hash;
  if (hash) {
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${window.location.search}`,
    );
  }
  const token = new URLSearchParams(hash.replace(/^#/, "")).get("token");
  return token || null;
}
