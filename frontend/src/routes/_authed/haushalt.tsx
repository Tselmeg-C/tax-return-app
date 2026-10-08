import { createFileRoute } from "@tanstack/react-router";

import { HouseholdPage } from "@/components/household/HouseholdPage";
import { belegeSearch } from "@/lib/taxItems";

export const Route = createFileRoute("/_authed/haushalt")({
  head: () => ({
    meta: [
      { title: "Haushalt — belegbot" },
      {
        name: "description",
        content:
          "Haushaltsmitglieder, Steuerklassen, Kinder und Veranlagungsart pro Jahr verwalten.",
      },
      { property: "og:title", content: "Haushalt — belegbot" },
      { property: "og:description", content: "Mitglieder, Kinder und Steuerprofil des Haushalts." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  validateSearch: belegeSearch,
  component: HouseholdPage,
});
