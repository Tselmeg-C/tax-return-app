import { createFileRoute } from "@tanstack/react-router";

import { SettingsPage } from "@/components/settings/SettingsPage";

export const Route = createFileRoute("/_authed/einstellungen")({
  head: () => ({
    meta: [
      { title: "Einstellungen — belegbot" },
      { name: "description", content: "Passkeys für die Anmeldung verwalten." },
      { property: "og:title", content: "Einstellungen — belegbot" },
      { property: "og:description", content: "Passkeys für die Anmeldung verwalten." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: SettingsPage,
});
