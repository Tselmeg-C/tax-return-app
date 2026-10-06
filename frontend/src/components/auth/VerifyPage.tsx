import { useQueryClient } from "@tanstack/react-query";
import { Link, useRouter } from "@tanstack/react-router";
import { useLayoutEffect, useRef, useState } from "react";

import { AuthCard, primaryButton } from "@/components/auth/AuthCard";
import { apiFetch } from "@/lib/api";
import { ME_KEY } from "@/lib/auth";
import { GENERIC_ERROR_TEXT, INVALID_LINK_TEXT, readAndStripToken } from "@/lib/login";

type Status = "reading" | "ready" | "sending" | "invalid" | "failed";

export function VerifyPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  // A ref survives StrictMode's double effect run (the fragment is gone after the first).
  const tokenRef = useRef<string | null | undefined>(undefined);
  const [status, setStatus] = useState<Status>("reading");

  useLayoutEffect(() => {
    if (tokenRef.current === undefined) tokenRef.current = readAndStripToken();
    setStatus(tokenRef.current ? "ready" : "invalid");
  }, []);

  async function signIn() {
    const token = tokenRef.current;
    if (!token) return;
    setStatus("sending");
    try {
      const { next } = await apiFetch<{ next: string }>("/auth/verify", {
        method: "POST",
        json: { token },
        redirectOn401: false,
      });
      tokenRef.current = null;
      await queryClient.invalidateQueries({ queryKey: ME_KEY });
      await router.navigate({ href: next || "/" });
    } catch (error) {
      const status =
        error && typeof error === "object" && "status" in error ? error.status : undefined;
      setStatus(status === 400 ? "invalid" : "failed");
    }
  }

  if (status === "invalid") {
    return (
      <AuthCard title="Anmelden">
        <p role="alert">{INVALID_LINK_TEXT}</p>
        <Link to="/login" className="block text-center text-sm text-primary underline">
          Neuen Link anfordern
        </Link>
      </AuthCard>
    );
  }

  return (
    <AuthCard title="Anmelden">
      <p className="text-muted-foreground">
        Klicke auf „Anmelden“, um dich bei belegbot anzumelden.
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
        onClick={signIn}
      >
        Anmelden
      </button>
    </AuthCard>
  );
}
