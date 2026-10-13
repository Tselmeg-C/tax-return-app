import { Link } from "@tanstack/react-router";
import { useLayoutEffect, useRef, useState } from "react";

import { AuthCard, primaryButton } from "@/components/auth/AuthCard";
import { apiFetch } from "@/lib/api";
import { GENERIC_ERROR_TEXT, INVALID_LINK_TEXT, readAndStripToken } from "@/lib/login";

type Status = "reading" | "ready" | "sending" | "done" | "invalid" | "failed";

/** Confirms a new login address (#34). Needs no session; all sessions end on success. */
export function ConfirmEmailPage() {
  const tokenRef = useRef<string | null | undefined>(undefined);
  const [status, setStatus] = useState<Status>("reading");

  useLayoutEffect(() => {
    if (tokenRef.current === undefined) tokenRef.current = readAndStripToken();
    setStatus(tokenRef.current ? "ready" : "invalid");
  }, []);

  async function confirm() {
    const token = tokenRef.current;
    if (!token) return;
    setStatus("sending");
    try {
      await apiFetch("/auth/confirm-email", {
        method: "POST",
        json: { token },
        redirectOn401: false,
      });
      tokenRef.current = null;
      setStatus("done");
    } catch (error) {
      const code =
        error && typeof error === "object" && "status" in error ? error.status : undefined;
      setStatus(code === 400 ? "invalid" : "failed");
    }
  }

  if (status === "invalid") {
    return (
      <AuthCard title="E-Mail bestätigen">
        <p role="alert">{INVALID_LINK_TEXT}</p>
        <Link to="/login" className="block text-center text-sm text-primary underline">
          Zur Anmeldung
        </Link>
      </AuthCard>
    );
  }
  if (status === "done") {
    return (
      <AuthCard title="E-Mail bestätigt">
        <p>Deine neue Adresse gilt ab jetzt. Bitte melde dich damit neu an.</p>
        <Link to="/login" className={primaryButton}>
          Zur Anmeldung
        </Link>
      </AuthCard>
    );
  }
  return (
    <AuthCard title="E-Mail bestätigen">
      <p className="text-muted-foreground">
        Klicke auf „Bestätigen“, damit diese Adresse künftig für deine Anmeldung gilt.
      </p>
      {status === "failed" && (
        <p role="alert" className="text-sm text-destructive">
          {GENERIC_ERROR_TEXT}
        </p>
      )}
      <button
        type="button"
        className={primaryButton}
        disabled={status !== "ready" && status !== "failed"}
        onClick={confirm}
      >
        Bestätigen
      </button>
    </AuthCard>
  );
}
