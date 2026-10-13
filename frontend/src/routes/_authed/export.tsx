import { useQuery } from "@tanstack/react-query";
import { createFileRoute, Link } from "@tanstack/react-router";
import { FileArchive, FileSpreadsheet, FileText } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AppShell } from "@/components/AppShell";
import { ApiError } from "@/lib/api";
import { downloadZip, EXPORT_TEXT, exportUrl } from "@/lib/exportFiles";
import { formatEur } from "@/lib/money";
import { fetchSummary, summaryKey } from "@/lib/summary";
import { belegeSearch, label } from "@/lib/taxItems";
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

const CARD = "sheet flex items-center gap-4 p-5 text-left transition-transform";

export function Export() {
  const { jahr, select, labels, isSupported } = useYear();
  const [zipBusy, setZipBusy] = useState(false);
  const summary = useQuery({
    queryKey: summaryKey(jahr),
    queryFn: () => fetchSummary(jahr),
    enabled: isSupported,
    retry: false,
  });
  const ready = isSupported && summary.data?.status === "ok";
  const noProfile = isSupported && summary.data?.status === "no_profile";
  // Same numbers as the files: `by_anlage` sums and `by_line` lines of GET /summary.
  const lines = summary.data?.by_line ?? [];
  const num = (z: string | null) => (z && /^\d+$/.test(z) ? Number(z) : Number.POSITIVE_INFINITY);

  const zip = async () => {
    setZipBusy(true);
    try {
      const result = await downloadZip(jahr);
      if (!result.ok) {
        toast.error(
          result.reason === "too_large" ? EXPORT_TEXT.tooLarge(result.maxMb) : EXPORT_TEXT.failed,
        );
      } else if (result.missing > 0) {
        toast.warning(EXPORT_TEXT.missing(result.missing));
      }
    } catch {
      toast.error(EXPORT_TEXT.failed);
    } finally {
      setZipBusy(false);
    }
  };

  const cards = [
    { icon: FileText, title: "PDF-Bericht", desc: "Nach Anlage & Zeile sortiert", format: "pdf" },
    { icon: FileSpreadsheet, title: "CSV", desc: "Für Excel / Numbers", format: "csv" },
  ] as const;

  return (
    <AppShell {...(select ? { year: select } : {})}>
      <h1 className="text-4xl">Export · Steuerjahr {jahr}</h1>
      <div className="mt-6 grid gap-4 sm:grid-cols-3">
        {cards.map(({ icon: Icon, title, desc, format }) => {
          const inner = (
            <>
              <Icon className="h-8 w-8 text-primary" />
              <div>
                <p className="font-display text-lg">{title}</p>
                <p className="text-xs text-muted-foreground">{desc}</p>
              </div>
            </>
          );
          return ready ? (
            <a
              key={format}
              href={exportUrl(jahr, format)}
              download
              className={`${CARD} hover:-translate-y-0.5`}
            >
              {inner}
            </a>
          ) : (
            <div key={format} aria-disabled="true" className={`${CARD} opacity-50`}>
              {inner}
            </div>
          );
        })}
        <button
          type="button"
          onClick={() => void zip()}
          disabled={!ready || zipBusy}
          className={`${CARD} hover:-translate-y-0.5 disabled:opacity-50 disabled:hover:translate-y-0`}
        >
          <FileArchive className="h-8 w-8 text-primary" />
          <div>
            <p className="font-display text-lg">Belege-ZIP</p>
            <p className="text-xs text-muted-foreground">
              {zipBusy ? EXPORT_TEXT.zipBusy : "Alle Originale"}
            </p>
          </div>
        </button>
      </div>
      {noProfile ? (
        <p className="mt-4 text-sm text-muted-foreground">
          {EXPORT_TEXT.needProfile}.{" "}
          <Link to="/haushalt" search={{ jahr }} className="underline">
            Zum Haushalt
          </Link>
        </p>
      ) : null}

      <div className="sheet mt-8 p-8">
        <p className="stamp text-muted-foreground">Vorschau · zum Übertragen in ELSTER</p>
        {summary.isError && !(summary.error instanceof ApiError && summary.error.status === 409) ? (
          <p role="alert" className="mt-4 text-sm text-destructive">
            Belege konnten nicht geladen werden.
          </p>
        ) : null}
        {summary.isPending && isSupported ? (
          <p className="mt-4 text-sm text-muted-foreground">Belege werden geladen…</p>
        ) : null}
        {(summary.data?.by_anlage ?? []).map((a) => (
          <section key={a.anlage} className="mt-6">
            <h2 className="border-b pb-1 text-xl">{label(labels.data?.anlagen, a.anlage)}</h2>
            <table className="mt-2 w-full text-sm">
              <tbody>
                {lines
                  .filter((l) => l.anlage === a.anlage)
                  .sort((x, y) => num(x.zeile) - num(y.zeile))
                  .map((l) => (
                    <tr key={l.category} className="border-b border-dashed">
                      <td className="num w-20 py-2 text-muted-foreground">{l.zeile ?? ""}</td>
                      <td className="py-2">
                        {label(labels.data?.categories, l.category)}{" "}
                        <span className="text-muted-foreground">
                          · {l.item_ids.length} {l.item_ids.length === 1 ? "Beleg" : "Belege"}
                        </span>
                      </td>
                      <td className="num py-2 text-right">{formatEur(l.amount)}</td>
                    </tr>
                  ))}
                <tr>
                  <td />
                  <td className="py-2 font-medium">Summe</td>
                  <td className="num py-2 text-right font-medium">{formatEur(a.amount)}</td>
                </tr>
              </tbody>
            </table>
          </section>
        ))}
      </div>
    </AppShell>
  );
}
