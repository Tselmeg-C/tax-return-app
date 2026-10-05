import { createFileRoute } from "@tanstack/react-router";
import { Baby, Plus, User } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { eur, members } from "@/lib/mock";

export const Route = createFileRoute("/haushalt")({
  head: () => ({
    meta: [
      { title: "Haushalt — belegbot" },
      { name: "description", content: "Haushaltsmitglieder, Steuerklassen, Kinder und Veranlagungsart pro Jahr verwalten." },
      { property: "og:title", content: "Haushalt — belegbot" },
      { property: "og:description", content: "Mitglieder, Kinder und Steuerprofil des Haushalts." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Haushalt,
});

function Haushalt() {
  return (
    <AppShell>
      <h1 className="text-4xl">Haushalt</h1>
      <div className="sheet mt-6 grid gap-4 p-6 sm:grid-cols-4">
        {[["Veranlagung", "Zusammen"], ["Bundesland", "Bayern"], ["Kirchensteuer", "keine"], ["Steuerjahr", "2025"]].map(([k, v]) => (
          <div key={k}><p className="stamp text-muted-foreground">{k}</p><p className="mt-1 font-display text-xl">{v}</p></div>
        ))}
      </div>

      <div className="mt-8 grid gap-5 md:grid-cols-3">
        {members.map((m) => (
          <div key={m.name} className="sheet p-6">
            <div className="flex items-center gap-3">
              <span className="flex h-10 w-10 items-center justify-center rounded-full bg-secondary">
                {m.kind === "child" ? <Baby className="h-5 w-5" /> : <User className="h-5 w-5" />}
              </span>
              <div>
                <p className="font-display text-xl">{m.name}</p>
                <p className="num text-xs text-muted-foreground">geb. {new Date(m.dob).toLocaleDateString("de-DE")}</p>
              </div>
            </div>
            <dl className="mt-5 space-y-2 border-t border-dashed pt-4 text-sm">
              {m.kind === "adult" ? (
                <>
                  <Row k="Arbeitgeber" v={m.employer!} />
                  <Row k="Steuerklasse" v={m.steuerklasse!} />
                  <Row k="Arbeitsweg" v={`${m.commuteKm} km`} />
                  <Row k="Homeoffice-Tage" v={String(m.homeofficeDays)} />
                </>
              ) : (
                <>
                  <Row k="Kindergeld" v={`${m.kindergeldMonths} Monate`} />
                  <Row k="Betreuungskosten" v={eur(m.betreuung!)} />
                </>
              )}
            </dl>
          </div>
        ))}
        <button className="flex min-h-48 flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed text-muted-foreground hover:border-primary hover:text-primary">
          <Plus className="h-6 w-6" /> Person hinzufügen
        </button>
      </div>

      <div className="sheet mt-8 flex flex-wrap items-center justify-between gap-4 p-6">
        <div>
          <p className="font-display text-xl">Telegram verknüpfen</p>
          <p className="text-sm text-muted-foreground">Sende diesen Code an den Bot: <span className="num">/link 482913</span></p>
        </div>
        <span className="num rounded-md bg-ink px-4 py-2 text-2xl tracking-widest text-paper">482 913</span>
      </div>
    </AppShell>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return <div className="flex justify-between"><dt className="text-muted-foreground">{k}</dt><dd className="num">{v}</dd></div>;
}
