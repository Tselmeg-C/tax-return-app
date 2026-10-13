import { useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { LogOut, Settings, UserRound } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/auth";

/** Signed-in e-mail plus "Abmelden" / "Auf allen Geräten abmelden". */
export function UserMenu() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

  const rootRef = useRef<HTMLDivElement>(null);
  const toggleRef = useRef<HTMLButtonElement>(null);

  // Escape (focus back to the toggle) and a click outside close the menu (#71).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      setOpen(false);
      toggleRef.current?.focus();
    };
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  async function logout(path: "/auth/logout" | "/auth/logout-all") {
    try {
      await apiFetch(path, { method: "POST", redirectOn401: false });
    } finally {
      // Never show cached data after logout, whatever the api answered.
      queryClient.clear();
      await navigate({ to: "/login", replace: true });
    }
  }

  if (!me) return null;
  return (
    <div ref={rootRef} className="relative min-w-0 shrink">
      <button
        ref={toggleRef}
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        title={me.email}
        aria-label={me.email}
        className="flex max-w-[12rem] items-center rounded-md border bg-card px-2 py-1 text-sm"
      >
        {/* Phone width: an icon only, so the header never scrolls horizontally (375 px). */}
        <UserRound className="h-4 w-4 sm:hidden" />
        <span className="hidden truncate sm:inline">{me.email}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-56 max-w-[calc(100vw-2rem)] rounded-md border bg-card p-1 text-sm shadow"
        >
          <Link
            to="/einstellungen"
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-secondary"
          >
            <Settings className="h-4 w-4" /> Einstellungen
          </Link>
          <button
            type="button"
            role="menuitem"
            onClick={() => void logout("/auth/logout")}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-secondary"
          >
            <LogOut className="h-4 w-4" /> Abmelden
          </button>
          <button
            type="button"
            role="menuitem"
            onClick={() => void logout("/auth/logout-all")}
            className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left hover:bg-secondary"
          >
            <LogOut className="h-4 w-4" /> Auf allen Geräten abmelden
          </button>
        </div>
      )}
    </div>
  );
}
