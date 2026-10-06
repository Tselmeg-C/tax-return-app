import { useNavigate, useSearch } from "@tanstack/react-router";
import { useState, type FormEvent } from "react";

import { AuthCard, primaryButton } from "@/components/auth/AuthCard";
import { rememberLoginRequest, requestMagicLink } from "@/lib/auth";
import { errorText } from "@/lib/login";

export function LoginPage() {
  const search = useSearch({ strict: false }) as { next?: string };
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

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
    </AuthCard>
  );
}
