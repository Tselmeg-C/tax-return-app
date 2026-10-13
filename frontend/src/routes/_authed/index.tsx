import { createFileRoute } from "@tanstack/react-router";
import { Dashboard } from "@/components/Dashboard";
import { belegeSearch } from "@/lib/taxItems";

export const Route = createFileRoute("/_authed/")({
  head: () => ({
    meta: [
      { title: "Übersicht — belegbot Steuererklärung" },
      {
        name: "description",
        content:
          "Erstattungsschätzung, Summen je Anlage und fehlende Unterlagen für deine Einkommensteuer.",
      },
      { property: "og:title", content: "Übersicht — belegbot Steuererklärung" },
      {
        property: "og:description",
        content: "Erstattungsschätzung und Summen je Anlage auf einen Blick.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  validateSearch: belegeSearch,
  component: Dashboard,
});
