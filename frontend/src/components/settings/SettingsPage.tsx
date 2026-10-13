import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/AppShell";
import {
  deletePasskey,
  PASSKEY_LIMIT_TEXT,
  PASSKEY_SETUP_FAILED_TEXT,
  PASSKEYS_KEY,
  passkeysSupported,
  registerPasskey,
  renamePasskey,
  usePasskeys,
  type Passkey,
} from "@/lib/passkeys";
import { DEVICES_TEXT, endOwnSession, SESSIONS_KEY, useOwnSessions } from "@/lib/users";

const date = (iso: string) => new Date(iso).toLocaleDateString("de-DE");

function PasskeyRow({ passkey, onChanged }: { passkey: Passkey; onChanged: () => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(passkey.name);
  const [error, setError] = useState(false);

  async function run(action: () => Promise<unknown>) {
    setError(false);
    try {
      await action();
      setEditing(false);
      await onChanged();
    } catch {
      setError(true);
    }
  }

  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <div className="min-w-0 flex-1">
        {editing ? (
          <input
            aria-label="Name des Passkeys"
            value={name}
            maxLength={60}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border bg-card px-2 py-1"
          />
        ) : (
          <p className="truncate font-medium">{passkey.name}</p>
        )}
        <p className="text-xs text-muted-foreground">
          Erstellt am {date(passkey.created_at)}
          {" · "}
          {passkey.last_used_at
            ? `zuletzt benutzt am ${date(passkey.last_used_at)}`
            : "noch nicht benutzt"}
        </p>
        {error && (
          <p role="alert" className="text-xs text-destructive">
            Das hat nicht geklappt, bitte erneut versuchen
          </p>
        )}
      </div>
      {editing ? (
        <>
          <button
            type="button"
            className="rounded-md bg-primary px-3 py-1 text-primary-foreground"
            onClick={() => void run(() => renamePasskey(passkey.id, name))}
          >
            Speichern
          </button>
          <button type="button" className="px-2 py-1" onClick={() => setEditing(false)}>
            Abbrechen
          </button>
        </>
      ) : (
        <>
          <button
            type="button"
            className="rounded-md border bg-card px-3 py-1 hover:bg-secondary"
            onClick={() => setEditing(true)}
          >
            Umbenennen
          </button>
          <button
            type="button"
            className="rounded-md border bg-card px-3 py-1 text-destructive hover:bg-secondary"
            onClick={() => {
              if (window.confirm(`Passkey „${passkey.name}“ wirklich entfernen?`))
                void run(() => deletePasskey(passkey.id));
            }}
          >
            Entfernen
          </button>
        </>
      )}
    </li>
  );
}

function DevicesCard() {
  const queryClient = useQueryClient();
  const { data, isError } = useOwnSessions();
  const [failed, setFailed] = useState(false);

  async function signOut(id: string) {
    setFailed(false);
    try {
      await endOwnSession(id);
      await queryClient.invalidateQueries({ queryKey: SESSIONS_KEY });
    } catch {
      setFailed(true);
    }
  }

  return (
    <section className="sheet mt-6 p-5" aria-labelledby="devices-title">
      <h2 id="devices-title" className="text-lg">
        {DEVICES_TEXT.title}
      </h2>
      <p className="mt-1 text-sm text-muted-foreground">{DEVICES_TEXT.intro}</p>
      {(isError || failed) && (
        <p role="alert" className="mt-3 text-sm text-destructive">
          {isError ? DEVICES_TEXT.loadError : DEVICES_TEXT.failed}
        </p>
      )}
      {data && (
        <ul className="mt-3 divide-y text-sm">
          {data.map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-3 py-3">
              <div className="min-w-0 flex-1">
                <p className="font-medium">
                  Angemeldet am {date(s.created_at)}
                  {s.current ? ` (${DEVICES_TEXT.current})` : ""}
                </p>
                <p className="text-xs text-muted-foreground">
                  zuletzt aktiv am {date(s.last_seen_at)}
                </p>
              </div>
              <button
                type="button"
                className="rounded-md border bg-card px-3 py-1 hover:bg-secondary"
                onClick={() => void signOut(s.id)}
              >
                {DEVICES_TEXT.signOut}
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export function SettingsPage() {
  const queryClient = useQueryClient();
  const [supported, setSupported] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  useEffect(() => setSupported(passkeysSupported()), []);
  const { data, isError } = usePasskeys();
  const refresh = () => queryClient.invalidateQueries({ queryKey: PASSKEYS_KEY });

  async function add() {
    setPending(true);
    setMessage(null);
    const outcome = await registerPasskey();
    setPending(false);
    if (outcome === "ok") await refresh();
    else if (outcome === "limit") setMessage(PASSKEY_LIMIT_TEXT);
    else if (outcome === "failed") setMessage(PASSKEY_SETUP_FAILED_TEXT);
  }

  return (
    <AppShell>
      <h1 className="text-2xl">Einstellungen</h1>
      <section className="sheet mt-6 p-5" aria-labelledby="passkeys-title">
        <h2 id="passkeys-title" className="text-lg">
          Passkeys
        </h2>
        <p className="mt-1 text-sm text-muted-foreground">
          Mit einem Passkey meldest du dich ohne E-Mail-Link an. Der Link per E-Mail funktioniert
          immer weiter.
        </p>
        {isError && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            Die Passkeys konnten nicht geladen werden.
          </p>
        )}
        {data && data.length === 0 && (
          <p className="mt-3 text-sm">Noch kein Passkey eingerichtet.</p>
        )}
        {data && data.length > 0 && (
          <ul className="mt-3 divide-y text-sm">
            {data.map((p) => (
              <PasskeyRow key={p.id} passkey={p} onChanged={refresh} />
            ))}
          </ul>
        )}
        {message && (
          <p role="alert" className="mt-3 text-sm text-destructive">
            {message}
          </p>
        )}
        {supported ? (
          <button
            type="button"
            disabled={pending}
            onClick={() => void add()}
            className="mt-4 rounded-md bg-primary px-3 py-1.5 text-sm text-primary-foreground disabled:opacity-50"
          >
            Passkey hinzufügen
          </button>
        ) : (
          <p className="mt-4 text-xs text-muted-foreground">
            Dieser Browser unterstützt keine Passkeys.
          </p>
        )}
      </section>
      <DevicesCard />
    </AppShell>
  );
}
