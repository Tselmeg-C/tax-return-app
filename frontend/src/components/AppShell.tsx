import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { FileText, LayoutDashboard, Users, Download, Send } from "lucide-react";

const nav = [
  { to: "/", label: "Übersicht", icon: LayoutDashboard },
  { to: "/belege", label: "Belege", icon: FileText },
  { to: "/haushalt", label: "Haushalt", icon: Users },
  { to: "/export", label: "Export", icon: Download },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center gap-6 px-5 py-3">
          <Link to="/" className="flex items-baseline gap-1">
            <span className="font-display text-2xl font-semibold">belegbot</span>
            <span className="stamp text-primary">ESt</span>
          </Link>
          <nav className="flex flex-1 gap-1 overflow-x-auto">
            {nav.map(({ to, label, icon: Icon }) => (
              <Link
                key={to}
                to={to}
                activeOptions={{ exact: to === "/" }}
                className="flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
                activeProps={{ className: "bg-ink text-paper hover:bg-ink hover:text-paper" }}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden sm:inline">{label}</span>
              </Link>
            ))}
          </nav>
          <select className="num rounded-md border bg-card px-2 py-1 text-sm" defaultValue="2025" aria-label="Steuerjahr">
            <option>2025</option>
            <option>2026</option>
          </select>
          <span className="hidden items-center gap-1 text-xs text-muted-foreground md:flex">
            <Send className="h-3.5 w-3.5 text-primary" /> Telegram verbunden
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-6xl px-5 py-8">{children}</main>
      <footer className="mx-auto max-w-6xl px-5 pb-10 text-xs text-muted-foreground">
        Schätzung, keine Steuerberatung. Werte manuell in ELSTER/WISO übertragen.
      </footer>
    </div>
  );
}
