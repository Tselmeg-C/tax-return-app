import { useQuery } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { buttonClass, primaryClass } from "@/components/household/forms";
import { apiFetch } from "@/lib/api";

/** Telegram linking (#11), per user. The one-time code lives in this component's state only
 * (a plain call, never the query or mutation cache), is shown once and expires after 10 min. */

export const TELEGRAM_TEXT = {
  title: "Telegram verknüpfen",
  intro:
    "Schick Belege per Foto oder PDF an den Bot. Erzeuge hier einen Code und sende ihn dem Bot als /link CODE.",
  create: "Code erzeugen",
  linked: "Telegram ist verknüpft.",
  unlink: "Trennen",
  expired: "Der Code ist abgelaufen.",
  failed: "Das hat nicht geklappt. Bitte noch einmal versuchen.",
  unavailable: "Der Status ist gerade nicht verfügbar.",
  unlinked: "Telegram getrennt.",
} as const;

interface LinkState {
  linked: boolean;
  channel: "telegram";
}

interface IssuedCode {
  code: string;
  expires_at: string;
}

export const LINK_KEY = ["bot", "link"] as const;

function remaining(expiresAt: string, now: number): number {
  return Math.max(0, Math.ceil((Date.parse(expiresAt) - now) / 1000));
}

function mmss(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}

export function TelegramCard() {
  const [issued, setIssued] = useState<IssuedCode | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const link = useQuery({
    queryKey: LINK_KEY,
    queryFn: () => apiFetch<LinkState>("/bot/link"),
    retry: false,
    refetchInterval: issued ? 3000 : false, // notice the link as soon as the bot used the code
  });
  const linked = link.data?.linked === true;

  useEffect(() => {
    if (!issued) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [issued]);

  useEffect(() => {
    if (linked) setIssued(null);
  }, [linked]);

  const seconds = issued ? remaining(issued.expires_at, now) : 0;

  async function create() {
    setBusy(true);
    try {
      const body = await apiFetch<IssuedCode>("/bot/link-code", { method: "POST" });
      setNow(Date.now());
      setIssued(body);
    } catch {
      toast.error(TELEGRAM_TEXT.failed);
    } finally {
      setBusy(false);
    }
  }

  async function unlink() {
    setBusy(true);
    try {
      await apiFetch("/bot/link", { method: "DELETE" });
      setIssued(null);
      await link.refetch();
      toast.success(TELEGRAM_TEXT.unlinked);
    } catch {
      toast.error(TELEGRAM_TEXT.failed);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sheet space-y-3 p-4 sm:p-6" aria-labelledby="telegram-title">
      <h2 id="telegram-title" className="flex items-center gap-2 text-2xl">
        <Send className="h-5 w-5 text-primary" /> {TELEGRAM_TEXT.title}
      </h2>
      {link.isError ? (
        <p className="text-sm text-muted-foreground">{TELEGRAM_TEXT.unavailable}</p>
      ) : linked ? (
        <div className="flex flex-wrap items-center gap-3">
          <p className="text-sm">{TELEGRAM_TEXT.linked}</p>
          <button
            type="button"
            className={buttonClass}
            disabled={busy}
            onClick={() => void unlink()}
          >
            {TELEGRAM_TEXT.unlink}
          </button>
        </div>
      ) : issued && seconds > 0 ? (
        <div className="space-y-1">
          <p className="num text-3xl tracking-widest" data-testid="telegram-code">
            {issued.code}
          </p>
          <p className="text-sm text-muted-foreground">
            Sende <span className="num">/link {issued.code}</span> an den Bot. Gültig noch{" "}
            <span className="num">{mmss(seconds)}</span>.
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-sm text-muted-foreground">
            {issued ? TELEGRAM_TEXT.expired : TELEGRAM_TEXT.intro}
          </p>
          <button
            type="button"
            className={primaryClass}
            disabled={busy || link.isPending}
            onClick={() => void create()}
          >
            {TELEGRAM_TEXT.create}
          </button>
        </div>
      )}
    </section>
  );
}
