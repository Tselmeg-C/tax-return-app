import { createFileRoute } from "@tanstack/react-router";
import { useRef, useState } from "react";
import { Camera, Pencil, Send, Upload, Globe, Check } from "lucide-react";
import { AppShell } from "@/components/AppShell";
import { anlageLabel, eur, items as seed, type TaxItem } from "@/lib/mock";

export const Route = createFileRoute("/belege")({
  head: () => ({
    meta: [
      { title: "Belege — belegbot" },
      { name: "description", content: "Rechnungen hochladen, automatisch erkennen lassen und Zuordnung zu Anlage und Zeile prüfen." },
      { property: "og:title", content: "Belege — belegbot" },
      { property: "og:description", content: "Belege hochladen und automatisch der richtigen Anlage zuordnen." },
      { property: "og:type", content: "website" },
      { name: "twitter:card", content: "summary" },
    ],
  }),
  component: Belege,
});

function Belege() {
  const [list, setList] = useState<TaxItem[]>(seed);
  const [filter, setFilter] = useState<"alle" | "relevant" | "unsicher">("alle");
  const [editing, setEditing] = useState<string | null>(null);
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const addFiles = (files: FileList | null) => {
    if (!files) return;
    const added: TaxItem[] = Array.from(files).map((f, n) => ({
      id: `new-${Date.now()}-${n}`, vendor: f.name, date: new Date().toISOString().slice(0, 10), gross: 0, deductible: 0,
      anlage: "N", zeile: "…", category: "Wird analysiert…", person: "Haushalt", channel: "web", confidence: 0, relevant: true,
    }));
    setList((l) => [...added, ...l]);
  };

  const shown = list.filter((i) => filter === "alle" || (filter === "relevant" ? i.relevant : i.confidence < 0.8 && i.confidence > 0));
  const update = (id: string, patch: Partial<TaxItem>) =>
    setList((l) => l.map((i) => (i.id === id ? { ...i, ...patch, overridden: true } : i)));

  return (
    <AppShell>
      <h1 className="text-4xl">Belege</h1>
      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={(e) => { e.preventDefault(); setDrag(false); addFiles(e.dataTransfer.files); }}
        className={`mt-6 flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-10 text-center transition-colors ${drag ? "border-primary bg-primary/5" : "bg-card"}`}
      >
        <Upload className="h-8 w-8 text-primary" />
        <p className="font-display text-xl">Fotos oder PDFs hier ablegen</p>
        <p className="text-sm text-muted-foreground">Oder einfach an den Telegram-Bot schicken — er sortiert automatisch.</p>
        <div className="flex gap-2">
          <button onClick={() => fileRef.current?.click()} className="flex items-center gap-2 rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90">
            <Upload className="h-4 w-4" /> Dateien wählen
          </button>
          <label className="flex cursor-pointer items-center gap-2 rounded-md border bg-background px-4 py-2 text-sm hover:bg-secondary">
            <Camera className="h-4 w-4" /> Foto
            <input type="file" accept="image/*" capture="environment" className="hidden" onChange={(e) => addFiles(e.target.files)} />
          </label>
        </div>
        <input ref={fileRef} type="file" multiple accept="image/*,application/pdf" className="hidden" onChange={(e) => addFiles(e.target.files)} />
      </div>

      <div className="mt-8 flex gap-2">
        {(["alle", "relevant", "unsicher"] as const).map((f) => (
          <button key={f} onClick={() => setFilter(f)} className={`rounded-full border px-3 py-1 text-sm capitalize ${filter === f ? "bg-ink text-paper" : "bg-card hover:bg-secondary"}`}>{f}</button>
        ))}
      </div>

      <div className="sheet mt-4 overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="stamp text-left text-muted-foreground">
            <tr className="border-b">
              <th className="p-3">Beleg</th><th className="p-3">Person</th><th className="p-3">Zuordnung</th>
              <th className="p-3 text-right">Brutto</th><th className="p-3 text-right">Absetzbar</th><th className="p-3">Sicherheit</th><th />
            </tr>
          </thead>
          <tbody className="divide-y divide-dashed">
            {shown.map((i) => (
              <tr key={i.id} className={i.relevant ? "" : "text-muted-foreground"}>
                <td className="p-3">
                  <div className="flex items-center gap-2">
                    {i.channel === "telegram" ? <Send className="h-3.5 w-3.5 text-primary" /> : <Globe className="h-3.5 w-3.5 text-muted-foreground" />}
                    <div>
                      <p className="font-medium">{i.vendor}</p>
                      <p className="num text-xs text-muted-foreground">{new Date(i.date).toLocaleDateString("de-DE")}</p>
                    </div>
                  </div>
                </td>
                <td className="p-3">{i.person}</td>
                <td className="p-3">
                  {i.relevant ? (
                    <>
                      <p>{i.category}</p>
                      <p className="text-xs text-muted-foreground">{anlageLabel[i.anlage]} · {i.zeile}</p>
                    </>
                  ) : <span className="stamp">nicht steuerrelevant</span>}
                  {i.overridden && <span className="stamp mt-1 inline-block text-accent-foreground bg-accent px-1 rounded-sm">manuell</span>}
                </td>
                <td className="num p-3 text-right">{eur(i.gross)}</td>
                <td className="num p-3 text-right">
                  {editing === i.id ? (
                    <input type="number" step="0.01" defaultValue={i.deductible} autoFocus
                      onBlur={(e) => { update(i.id, { deductible: Number(e.target.value) }); setEditing(null); }}
                      onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                      className="w-24 rounded border bg-background px-2 py-1 text-right" />
                  ) : eur(i.deductible)}
                </td>
                <td className="p-3"><Confidence v={i.confidence} /></td>
                <td className="p-3">
                  <button onClick={() => setEditing(editing === i.id ? null : i.id)} aria-label="Bearbeiten" className="rounded p-1.5 hover:bg-secondary">
                    {editing === i.id ? <Check className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </AppShell>
  );
}

function Confidence({ v }: { v: number }) {
  if (v === 0) return <span className="stamp animate-pulse text-muted-foreground">läuft…</span>;
  const low = v < 0.8;
  return (
    <span className={`stamp rounded-sm px-1.5 py-0.5 ${low ? "bg-warning/20 text-foreground" : "bg-success/15 text-success"}`}>
      {low ? "unsicher" : "sicher"} {Math.round(v * 100)}%
    </span>
  );
}
