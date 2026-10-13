import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { FileArchive, FileSpreadsheet, FileText } from "lucide-react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { formatEur } from "@/lib/money";
import { belegeSearch, label, listTaxItems, TAX_ITEMS_KEY, type TaxItemOut } from "@/lib/taxItems";
import { useYear } from "@/lib/year";

export const Route = createFileRoute("/_authed/export")({
  head: () => ({
    meta: [
      { title: "Export — belegbot" },
      {
        name: "description",
        content:
          "Steuerübersicht nach Anlage und Zeile als PDF, CSV oder ZIP mit allen Originalbelegen exportieren.",
      },
      { property: "og:title", content: "Export — belegbot" },
      {
        property: "og:description",
        content: "PDF-Bericht, CSV und Belege-ZIP für ELSTER oder WISO.",
      },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  validateSearch: belegeSearch,
  component: Export,
});

function Export() {
  const { jahr, select, labels, isSupported } = useYear();
  const list = useQuery({
    queryKey: [...TAX_ITEMS_KEY, "export", jahr],
    queryFn: () => listTaxItems({ year: jahr, filter: "relevant" }),
    enabled: isSupported,
  });
  const rel = (list.data?.items ?? []).filter((i) => i.anlage);
  const groups = Object.entries(
    rel.reduce<Record<string, TaxItemOut[]>>((a, i) => ((a[i.anlage!] ??= []).push(i), a), {}),
  );
  // Sum in cents (integers): never float math on money.
  const cents = (v: string) => Math.round(Number(v) * 100);
  const sum = (l: TaxItemOut[]) =>
    (l.reduce((s, i) => s + cents(i.deductible_amount), 0) / 100).toFixed(2);

  const downloadCsv = () => {
    const rows = [
      ["Anlage", "Zeile", "Beleg", "Datum", "Absetzbar"],
      ...rel.map((i) => [
        i.anlage ?? "",
        i.zeile ?? "",
        i.vendor ?? "",
        i.invoice_date ?? "",
        i.deductible_amount,
      ]),
    ];
    const blob = new Blob([rows.map((r) => r.join(";")).join("\n")], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `steuer-${jahr}.csv`;
    a.click();
  };

  const formats = [
    {
      icon: FileText,
      title: "PDF-Bericht",
      desc: "Nach Anlage & Zeile sortiert",
      action: () => toast("PDF wird erstellt…"),
    },
    { icon: FileSpreadsheet, title: "CSV", desc: "Für Excel / Numbers", action: downloadCsv },
    {
      icon: FileArchive,
      title: "Belege-ZIP",
      desc: "Alle Originale",
      action: () => toast("ZIP wird gepackt…"),
    },
  ];

  return (
    <AppShell {...(select ? { year: select } : {})}>
      <h1 className="text-4xl">Export · Steuerjahr {jahr}</h1>
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {formats.map(({ icon: Icon, title, desc, action }) => (
          <button
            key={title}
            onClick={action}
            className="sheet flex items-center gap-4 p-5 text-left transition-transform hover:-translate-y-0.5"
          >
            <Icon className="h-8 w-8 text-primary" />
            <div>
              <p className="font-display text-lg">{title}</p>
              <p className="text-xs text-muted-foreground">{desc}</p>
            </div>
          </button>
        ))}
      </div>

      <div className="sheet mt-8 p-8">
        <p className="stamp text-muted-foreground">Vorschau · zum Übertragen in ELSTER</p>
        {list.isError ? (
          <p role="alert" className="mt-4 text-sm text-destructive">
            Belege konnten nicht geladen werden.
          </p>
        ) : null}
        {list.isPending && isSupported ? (
          <p className="mt-4 text-sm text-muted-foreground">Belege werden geladen…</p>
        ) : null}
        {groups.map(([a, group]) => (
          <section key={a} className="mt-6">
            <h2 className="border-b pb-1 text-xl">{label(labels.data?.anlagen, a)}</h2>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {group.map((i) => (
                  <tr key={i.id} className="border-b border-dashed">
                    <td className="num w-20 py-2 text-muted-foreground">{i.zeile ?? ""}</td>
                    <td className="py-2">
                      {label(labels.data?.categories, i.category)}{" "}
                      <span className="text-muted-foreground">
                        {i.vendor ? `· ${i.vendor}` : ""}
                      </span>
                    </td>
                    <td className="num py-2 text-right">{formatEur(i.deductible_amount)}</td>
                  </tr>
                ))}
                <tr>
                  <td />
                  <td className="py-2 font-medium">Summe</td>
                  <td className="num py-2 text-right font-medium">{formatEur(sum(group))}</td>
                </tr>
              </tbody>
            </table>
          </section>
        ))}
      </div>
    </AppShell>
  );
}
