/** Export (#21): download URLs and the ZIP download (a `fetch` so `X-Export-Missing` is readable). */

export type ExportFormat = "pdf" | "csv" | "zip";

export const exportUrl = (jahr: number, format: ExportFormat) => `/api/export/${jahr}.${format}`;

export const EXPORT_TEXT = {
  needProfile: "Erst Haushalt anlegen",
  zipBusy: "ZIP wird gepackt…",
  tooLarge: (mb: number) =>
    `Die Originale sind zu groß für ein ZIP (mehr als ${mb} MB). Lade die Belege einzeln herunter.`,
  failed: "Der Export ist fehlgeschlagen. Bitte versuche es noch einmal.",
  missing: (n: number) =>
    n === 1
      ? "1 Original konnte nicht gelesen werden und fehlt im ZIP (siehe FEHLT.txt)."
      : `${n} Originale konnten nicht gelesen werden und fehlen im ZIP (siehe FEHLT.txt).`,
} as const;

export type ZipResult =
  | { ok: true; missing: number }
  | { ok: false; reason: "too_large"; maxMb: number }
  | { ok: false; reason: "failed" };

/** Fetches the ZIP and hands it to the browser as a download. */
export async function downloadZip(jahr: number): Promise<ZipResult> {
  const response = await fetch(exportUrl(jahr, "zip"), { credentials: "same-origin" });
  if (response.status === 413) {
    const body = (await response.json().catch(() => ({}))) as { max_bytes?: number };
    return { ok: false, reason: "too_large", maxMb: Math.round((body.max_bytes ?? 0) / 1048576) };
  }
  if (!response.ok) return { ok: false, reason: "failed" };
  const missing = Number(response.headers.get("X-Export-Missing") ?? "0") || 0;
  const url = URL.createObjectURL(await response.blob());
  const a = document.createElement("a");
  a.href = url;
  a.download = `belegbot-${jahr}-belege.zip`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  return { ok: true, missing };
}
