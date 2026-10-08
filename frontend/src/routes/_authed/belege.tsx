import { createFileRoute } from "@tanstack/react-router";

import { BelegePage } from "@/components/documents/BelegePage";
import { belegeSearch } from "@/lib/taxItems";

export const Route = createFileRoute("/_authed/belege")({
  validateSearch: belegeSearch,
  head: () => ({
    meta: [
      { title: "Belege — belegbot" },
      {
        name: "description",
        content:
          "Rechnungen hochladen, automatisch erkennen lassen und Zuordnung zu Anlage und Zeile prüfen.",
      },
      { property: "og:title", content: "Belege — belegbot" },
      {
        property: "og:description",
        content: "Belege hochladen und automatisch der richtigen Anlage zuordnen.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: BelegePage,
});
