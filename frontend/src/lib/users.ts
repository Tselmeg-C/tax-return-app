/** Member management (#34): owner-only `/users*` and own sessions `/auth/sessions*`.
 * E-mail addresses are shown but never put into the query key, a URL or storage. */
import { useQuery } from "@tanstack/react-query";

import { ApiError, apiFetch } from "@/lib/api";

export const USERS_KEY = ["users"] as const;
export const SESSIONS_KEY = ["auth", "sessions"] as const;

export interface UserOut {
  id: string;
  email: string;
  role: "owner" | "member";
  disabled: boolean;
  last_login_at: string | null;
  person_id: string | null;
  is_self: boolean;
}

export interface SessionOut {
  id: string;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export const ACCESS_TEXT = {
  title: "Zugänge",
  intro: "Wer sich bei belegbot anmelden darf. Nur Inhaber verwalten die Zugänge.",
  inviteEmail: "E-Mail-Adresse einladen",
  invite: "Einladen",
  invited: "Einladung verschickt.",
  owner: "Inhaber",
  member: "Mitglied",
  disabled: "gesperrt",
  never: "noch nie angemeldet",
  makeOwner: "Zum Inhaber machen",
  makeMember: "Zum Mitglied machen",
  disable: "Zugang sperren",
  disableHint: "Hochgeladene Belege bleiben erhalten.",
  enable: "Zugang freigeben",
  endSessions: "Alle Geräte abmelden",
  changeEmail: "E-Mail ändern",
  newEmail: "Neue E-Mail-Adresse",
  send: "Bestätigung senden",
  cancel: "Abbrechen",
  emailSent:
    "Bestätigungslink an die neue Adresse geschickt. Die Änderung gilt erst nach dem Klick.",
  sessionsEnded: "Geräte abgemeldet.",
  lastOwner: "Der letzte aktive Inhaber kann nicht gesperrt oder herabgestuft werden.",
  failed: "Das hat nicht geklappt. Bitte noch einmal versuchen.",
  loadError: "Die Zugänge konnten nicht geladen werden.",
  saved: "Gespeichert.",
} as const;

export const DEVICES_TEXT = {
  title: "Angemeldete Geräte",
  intro: "Hier siehst du, wo du gerade angemeldet bist.",
  current: "dieses Gerät",
  signOut: "Abmelden",
  failed: "Das hat nicht geklappt. Bitte noch einmal versuchen.",
  loadError: "Die Geräte konnten nicht geladen werden.",
} as const;

const CODES: Record<string, string> = {
  last_owner: ACCESS_TEXT.lastOwner,
  email_taken: "Diese E-Mail-Adresse wird schon verwendet.",
  invalid_field: "Bitte eine gültige E-Mail-Adresse eingeben.",
  rate_limited: "Zu viele Versuche, bitte später erneut versuchen.",
};

/** A user-facing message for an api error (by its code, never the sent value). */
export function accessError(error: unknown): string {
  if (error instanceof ApiError && error.detail && CODES[error.detail]) return CODES[error.detail]!;
  return ACCESS_TEXT.failed;
}

export function useUsers(enabled: boolean) {
  return useQuery({
    queryKey: USERS_KEY,
    queryFn: () => apiFetch<UserOut[]>("/users"),
    enabled,
    retry: false,
  });
}

export function useOwnSessions() {
  return useQuery({
    queryKey: SESSIONS_KEY,
    queryFn: () => apiFetch<SessionOut[]>("/auth/sessions"),
  });
}

export const inviteUser = (email: string, role: "owner" | "member") =>
  apiFetch<UserOut>("/users", { method: "POST", json: { email, role } });

export const patchUser = (id: string, body: Record<string, unknown>) =>
  apiFetch<UserOut>(`/users/${id}`, { method: "PATCH", json: body });

export const endUserSessions = (id: string) =>
  apiFetch<{ ended: number }>(`/users/${id}/sessions`, { method: "DELETE" });

export const endOwnSession = (id: string) =>
  apiFetch<void>(`/auth/sessions/${id}`, { method: "DELETE" });
