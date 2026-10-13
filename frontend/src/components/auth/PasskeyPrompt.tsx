import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import {
  dismissPrompt,
  PASSKEY_SETUP_FAILED_TEXT,
  PASSKEYS_KEY,
  passkeysSupported,
  promptDismissed,
  registerPasskey,
  usePasskeys,
} from "@/lib/passkeys";

/** "Passkey einrichten?" once, while the user has none and has not said "Später". */
export function PasskeyPrompt() {
  const queryClient = useQueryClient();
  const [supported, setSupported] = useState(false);
  const [dismissed, setDismissed] = useState(true);
  const [error, setError] = useState(false);
  const [pending, setPending] = useState(false);
  useEffect(() => {
    setSupported(passkeysSupported());
    setDismissed(promptDismissed());
  }, []);
  const { data } = usePasskeys(supported && !dismissed);

  if (!supported || dismissed || !data || data.length > 0) return null;

  async function setup() {
    setPending(true);
    setError(false);
    const outcome = await registerPasskey();
    setPending(false);
    if (outcome === "ok") await queryClient.invalidateQueries({ queryKey: PASSKEYS_KEY });
    else if (outcome !== "cancelled") setError(true);
  }

  return (
    <div role="region" aria-label="Passkey einrichten" className="sheet mb-6 p-4 text-sm">
      <p className="font-medium">Passkey einrichten?</p>
      <p className="mt-1 text-muted-foreground">
        Melde dich künftig mit Face ID, Fingerabdruck oder Gerätesperre an, ohne auf eine E-Mail zu
        warten.
      </p>
      {error && (
        <p role="alert" className="mt-2 text-destructive">
          {PASSKEY_SETUP_FAILED_TEXT}
        </p>
      )}
      <div className="mt-3 flex gap-2">
        <button
          type="button"
          disabled={pending}
          onClick={() => void setup()}
          className="rounded-md bg-primary px-3 py-1.5 text-primary-foreground disabled:opacity-50"
        >
          Einrichten
        </button>
        <button
          type="button"
          onClick={() => {
            dismissPrompt();
            setDismissed(true);
          }}
          className="rounded-md border bg-card px-3 py-1.5 hover:bg-secondary"
        >
          Später
        </button>
      </div>
    </div>
  );
}
