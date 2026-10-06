import { Link } from "@tanstack/react-router";
import { useEffect, useState } from "react";

import { AuthCard, secondaryButton } from "@/components/auth/AuthCard";
import { lastLoginRequest, requestMagicLink } from "@/lib/auth";
import { errorText } from "@/lib/login";

export const RESEND_COOLDOWN_SECONDS = 60;
export const SENT_TEXT =
  "Falls diese Adresse für belegbot freigeschaltet ist, ist ein Anmeldelink unterwegs. Er ist 15 Minuten gültig.";

export function LoginSentPage() {
  // Counts down from the send that brought us here; every resend restarts it.
  const [remaining, setRemaining] = useState(RESEND_COOLDOWN_SECONDS);
  const [error, setError] = useState<string | null>(null);
  const request = lastLoginRequest();

  useEffect(() => {
    if (remaining <= 0) return;
    const timer = setTimeout(() => setRemaining((s) => s - 1), 1000);
    return () => clearTimeout(timer);
  }, [remaining]);

  async function resend() {
    if (!request) return;
    setError(null);
    setRemaining(RESEND_COOLDOWN_SECONDS);
    try {
      await requestMagicLink(request.email, request.next);
    } catch (err) {
      setError(errorText(err));
    }
  }

  return (
    <AuthCard title="Link unterwegs">
      <p>{SENT_TEXT}</p>
      {error && (
        <p role="alert" className="text-sm text-destructive">
          {error}
        </p>
      )}
      <button
        type="button"
        className={secondaryButton}
        disabled={remaining > 0 || !request}
        onClick={resend}
      >
        {remaining > 0 ? `Erneut senden (${remaining} s)` : "Erneut senden"}
      </button>
      <Link to="/login" className="block text-center text-sm text-primary underline">
        Andere Adresse verwenden
      </Link>
    </AuthCard>
  );
}
