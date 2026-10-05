import { createFileRoute } from "@tanstack/react-router";
import { FileArchive, FileSpreadsheet, FileText } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { anlageLabel, eur, items, type Anlage } from "@/lib/mock";

export const Route = createFileRoute("/export")({
  head: () => ({
    meta: [
      { title: "Export — belegbot" },
      { name: "description", content: "Steuerübersicht nach Anlage und Zeile als PDF, CSV oder ZIP mit allen Originalbelegen exportieren." },
      { property: "og:title", content: "Export — belegbot" },
      { property: "og:description", content: "PDF-Bericht, CSV und Belege-ZIP für ELSTER oder WISO." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Export,
});

function Export() {
  const rel = items.filter((i) => i.relevant);
  const groups = Object.entries(
    rel.reduce<Record<string, typeof rel>>((a, i) => ((a[i.anlage] ??= []).push(i), a), {}),
  );

  const downloadCsv = () => {
    const rows = [["Anlage", "Zeile", "Beleg", "Datum", "Absetzbar"], ...rel.map((i) => [i.anlage, i.zeile, i.vendor, i.date, i.deductible.toFixed(2)])];
    const blob = new Blob([rows.map((r) => r.join(";")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "steuer-2025.csv";
    a.click();
  };

  const formats = [
    { icon: FileText, title: "PDF-Bericht", desc: "Nach Anlage & Zeile sortiert", action: () => toast("PDF wird erstellt…") },
    { icon: FileSpreadsheet, title: "CSV", desc: "Für Excel / Numbers", action: downloadCsv },
    { icon: FileArchive, title: "Belege-ZIP", desc: "Alle Originale", action: () => toast("ZIP wird gepackt…") },
  ];

  return (
    <AppShell>
      <h1 className="text-4xl">Export 2025</h1>
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {formats.map(({ icon: Icon, title, desc, action }) => (
          <button key={title} onClick={action} className="sheet flex items-center gap-4 p-5 text-left transition-transform hover:-translate-y-0.5">
            <Icon className="h-8 w-8 text-primary" />
            <div><p className="font-display text-lg">{title}</p><p className="text-xs text-muted-foreground">{desc}</p></div>
          </button>
        ))}
      </div>

      <div className="sheet mt-8 p-8">
        <p className="stamp text-muted-foreground">Vorschau · zum Übertragen in ELSTER</p>
        {groups.map(([a, list]) => (
          <section key={a} className="mt-6">
            <h2 className="border-b pb-1 text-xl">{anlageLabel[a as Anlage]}</h2>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {list.map((i) => (
                  <tr key={i.id} className="border-b border-dashed">
                    <td className="num w-20 py-2 text-muted-foreground">{i.zeile}</td>
                    <td className="py-2">{i.category} <span className="text-muted-foreground">· {i.vendor}</span></td>
                    <td className="num py-2 text-right">{eur(i.deductible)}</td>
                  </tr>
                ))}
                <tr><td /><td className="py-2 font-medium">Summe</td><td className="num py-2 text-right font-medium">{eur(list.reduce((s, i) => s + i.deductible, 0))}</td></tr>
              </tbody>
            </table>
          </section>
        ))}
      </div>
    </AppShell>
  );
}
