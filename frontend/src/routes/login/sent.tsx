import { createFileRoute } from "@tanstack/react-router";

import { LoginSentPage } from "@/components/auth/LoginSentPage";

export const Route = createFileRoute("/login/sent")({
  head: () => ({ meta: [{ title: "Link unterwegs — belegbot" }] }),
  component: LoginSentPage,
});
