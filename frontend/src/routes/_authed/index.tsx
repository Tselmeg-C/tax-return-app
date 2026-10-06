import { createFileRoute, Link } from "@tanstack/react-router";
import { AlertTriangle, ArrowRight, CheckCircle2 } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { anlageLabel, estimate, eur, items, missing, type Anlage } from "@/lib/mock";

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
  component: Dashboard,
});

function Dashboard() {
  const totals = items
    .filter((i) => i.relevant)
    .reduce<Record<string, number>>((acc, i) => {
      acc[i.anlage] = (acc[i.anlage] ?? 0) + i.deductible;
      return acc;
    }, {});
  const max = Math.max(...Object.values(totals));
  const recent = items.slice(0, 4);

  return (
    <AppShell>
      <section className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <div className="sheet relative overflow-hidden p-8">
          <p className="stamp text-muted-foreground">
            Steuerjahr {estimate.year} · {estimate.assessment}
          </p>
          <h1 className="mt-4 text-lg font-normal text-muted-foreground">
            Voraussichtliche Erstattung
          </h1>
          <p className="num mt-1 text-6xl font-medium text-primary md:text-7xl">
            {eur(estimate.refund)}
          </p>
          <dl className="mt-8 grid grid-cols-3 gap-4 border-t border-dashed pt-5 text-sm">
            <div>
              <dt className="text-muted-foreground">zvE</dt>
              <dd className="num">{eur(estimate.zvE)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Festgesetzte ESt</dt>
              <dd className="num">{eur(estimate.festgesetzt)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Lohnsteuer gezahlt</dt>
              <dd className="num">{eur(estimate.lohnsteuerPaid)}</dd>
            </div>
          </dl>
          <span className="stamp absolute right-6 top-6 rotate-6 rounded border-2 border-primary px-2 py-1 text-primary">
            ±50 € Genauigkeit
          </span>
        </div>

        <div className="flex flex-col gap-4">
          <div className="sheet flex items-start gap-3 p-5">
            <CheckCircle2 className="mt-0.5 h-5 w-5 text-success" />
            <div>
              <p className="font-medium">Pflichtveranlagung</p>
              <p className="text-sm text-muted-foreground">
                Abgabe ist Pflicht (§46 EStG, Steuerklassen III/V). Frist: 31.07.2026.
              </p>
            </div>
          </div>
          <div className="sheet p-5">
            <p className="stamp mb-3 text-warning">Was fehlt noch</p>
            <ul className="space-y-3">
              {missing.map((m) => (
                <li key={m.title} className="flex gap-3">
                  <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  <div>
                    <p className="text-sm font-medium">{m.title}</p>
                    <p className="text-xs text-muted-foreground">{m.detail}</p>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section className="mt-10 grid gap-6 lg:grid-cols-2">
        <div className="sheet p-6">
          <h2 className="text-2xl">Summen je Anlage</h2>
          <ul className="mt-5 space-y-4">
            {Object.entries(totals)
              .sort((a, b) => b[1] - a[1])
              .map(([a, v]) => (
                <li key={a}>
                  <div className="flex justify-between text-sm">
                    <span>{anlageLabel[a as Anlage]}</span>
                    <span className="num">{eur(v)}</span>
                  </div>
                  <div className="mt-1.5 h-1.5 rounded-full bg-secondary">
                    <div
                      className="h-full rounded-full bg-primary"
                      style={{ width: `${(v / max) * 100}%` }}
                    />
                  </div>
                </li>
              ))}
          </ul>
        </div>
        <div className="sheet p-6">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl">Zuletzt erkannt</h2>
            <Link
              to="/belege"
              className="flex items-center gap-1 text-sm text-primary hover:underline"
            >
              Alle Belege <ArrowRight className="h-4 w-4" />
            </Link>
          </div>
          <ul className="mt-4 divide-y divide-dashed">
            {recent.map((i) => (
              <li key={i.id} className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium">{i.vendor}</p>
                  <p className="text-xs text-muted-foreground">
                    {i.category} → {anlageLabel[i.anlage]} {i.zeile}
                  </p>
                </div>
                <span className="num text-sm">{eur(i.deductible)}</span>
              </li>
            ))}
          </ul>
        </div>
      </section>
    </AppShell>
  );
}
