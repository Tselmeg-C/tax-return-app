import { createFileRoute } from "@tanstack/react-router";

import { VerifyPage } from "@/components/auth/VerifyPage";

// The login token is in the URL fragment (never sent to a server); the page strips it and
// only a click on "Anmelden" posts it. No Referer is ever sent from this page.
export const Route = createFileRoute("/login/verify")({
  head: () => ({
    meta: [{ title: "Anmelden — belegbot" }, { name: "referrer", content: "no-referrer" }],
  }),
  component: VerifyPage,
});
