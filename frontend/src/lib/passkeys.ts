/**
 * Passkeys (#12): browser-native WebAuthn (`navigator.credentials`, `parse*OptionsFromJSON`,
 * `toJSON`; Safari 18+, Chrome 129+). All api calls go through `apiFetch`. Nothing here is
 * logged; the list the api returns holds id, name and dates only.
 */
import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";

export const PASSKEYS_KEY = ["auth", "passkeys"] as const;
export const PASSKEY_DISMISSED_KEY = "belegbot.passkey-prompt-dismissed";
export const PASSKEY_FAILED_TEXT =
  "Anmeldung mit Passkey nicht möglich. Bitte nutze den Link per E-Mail.";
export const PASSKEY_SETUP_FAILED_TEXT = "Der Passkey konnte nicht eingerichtet werden.";
export const PASSKEY_LIMIT_TEXT = "Es sind höchstens 10 Passkeys möglich.";

export interface Passkey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
}

/** The browser can do passkeys (feature detection only, no polyfill). */
export function passkeysSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.PublicKeyCredential === "function" &&
    typeof window.PublicKeyCredential.parseCreationOptionsFromJSON === "function" &&
    typeof window.PublicKeyCredential.parseRequestOptionsFromJSON === "function" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.credentials)
  );
}

export type PasskeyOutcome = "ok" | "cancelled" | "failed";

function isCancel(error: unknown): boolean {
  return error instanceof DOMException && error.name === "NotAllowedError";
}

/** Discoverable login (no e-mail). `next` is the validated post-login path. */
export async function loginWithPasskey(
  next: string | undefined,
): Promise<{ outcome: "ok"; next: string } | { outcome: "cancelled" | "failed" }> {
  try {
    const options = await apiFetch<PublicKeyCredentialRequestOptionsJSON>(
      "/auth/passkey/login/options",
      { method: "POST", redirectOn401: false },
    );
    const credential = await navigator.credentials.get({
      publicKey: PublicKeyCredential.parseRequestOptionsFromJSON(options),
    });
    if (!(credential instanceof PublicKeyCredential)) return { outcome: "failed" };
    const body = await apiFetch<{ next: string }>("/auth/passkey/login/verify", {
      method: "POST",
      json: next ? { credential: credential.toJSON(), next } : { credential: credential.toJSON() },
      redirectOn401: false,
    });
    return { outcome: "ok", next: body.next || "/" };
  } catch (error) {
    return { outcome: isCancel(error) ? "cancelled" : "failed" };
  }
}

/** Registers a passkey for the signed-in user; `limit` = the 10 passkey maximum is reached. */
export async function registerPasskey(): Promise<PasskeyOutcome | "limit"> {
  try {
    const options = await apiFetch<PublicKeyCredentialCreationOptionsJSON>(
      "/auth/passkey/register/options",
      { method: "POST" },
    );
    const credential = await navigator.credentials.create({
      publicKey: PublicKeyCredential.parseCreationOptionsFromJSON(options),
    });
    if (!(credential instanceof PublicKeyCredential)) return "failed";
    await apiFetch("/auth/passkey/register/verify", {
      method: "POST",
      json: { credential: credential.toJSON() },
    });
    return "ok";
  } catch (error) {
    if (isCancel(error)) return "cancelled";
    const status =
      error && typeof error === "object" && "status" in error ? error.status : undefined;
    return status === 409 ? "limit" : "failed";
  }
}

export function usePasskeys(enabled = true) {
  return useQuery({
    queryKey: PASSKEYS_KEY,
    queryFn: () => apiFetch<Passkey[]>("/auth/passkeys"),
    enabled,
  });
}

export function renamePasskey(id: string, name: string): Promise<Passkey> {
  return apiFetch<Passkey>(`/auth/passkeys/${id}`, { method: "PATCH", json: { name } });
}

export function deletePasskey(id: string): Promise<void> {
  return apiFetch<void>(`/auth/passkeys/${id}`, { method: "DELETE" });
}

/** "Später" is a per-viewer convenience: localStorage only, never server state. */
export function promptDismissed(): boolean {
  try {
    return localStorage.getItem(PASSKEY_DISMISSED_KEY) === "1";
  } catch {
    return false;
  }
}

export function dismissPrompt(): void {
  try {
    localStorage.setItem(PASSKEY_DISMISSED_KEY, "1");
  } catch {
    // storage blocked: the prompt shows again next time
  }
}
