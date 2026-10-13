import { createFileRoute } from "@tanstack/react-router";

import { ConfirmEmailPage } from "@/components/auth/ConfirmEmailPage";

// Same fragment handling as `/login/verify`: only a click posts the token (#34).
export const Route = createFileRoute("/login/confirm-email")({
  head: () => ({
    meta: [{ title: "E-Mail bestätigen — belegbot" }, { name: "referrer", content: "no-referrer" }],
  }),
  component: ConfirmEmailPage,
});
