import { useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearch } from "@tanstack/react-router";
import { useEffect, useState, type FormEvent } from "react";

import { AuthCard, primaryButton, secondaryButton } from "@/components/auth/AuthCard";
import { ME_KEY, rememberLoginRequest, requestMagicLink } from "@/lib/auth";
import { errorText } from "@/lib/login";
import { loginWithPasskey, passkeysSupported, PASSKEY_FAILED_TEXT } from "@/lib/passkeys";

export function LoginPage() {
  const search = useSearch({ strict: false }) as { next?: string };
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const queryClient = useQueryClient();
  const [supported, setSupported] = useState(false);
  useEffect(() => setSupported(passkeysSupported()), []);

  async function onPasskey() {
    setPending(true);
    setError(null);
    const result = await loginWithPasskey(search.next);
    if (result.outcome === "ok") {
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await navigate({ href: result.next });
      return;
    }
    setPending(false);
    if (result.outcome === "failed") setError(PASSKEY_FAILED_TEXT);
  }

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      await requestMagicLink(email, search.next);
      rememberLoginRequest(email, search.next);
      await navigate({ to: "/login/sent" });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setPending(false);
    }
  }

  return (
    <AuthCard title="Anmelden">
      <p className="text-muted-foreground">
        Wir schicken dir einen Anmeldelink an deine E-Mail-Adresse.
      </p>
      <form onSubmit={onSubmit} className="space-y-4" noValidate>
        <label className="block">
          <span className="stamp text-muted-foreground">E-Mail-Adresse</span>
          <input
            type="email"
            name="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full rounded-md border bg-card px-3 py-2 text-sm"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        <button type="submit" className={primaryButton} disabled={pending}>
          Link senden
        </button>
      </form>
      {supported && (
        <button type="button" className={secondaryButton} disabled={pending} onClick={onPasskey}>
          Mit Passkey anmelden
        </button>
      )}
    </AuthCard>
  );
}
