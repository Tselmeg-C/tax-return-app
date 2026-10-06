import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { LogOut } from "lucide-react";
import { useState } from "react";

import { apiFetch } from "@/lib/api";
import { useMe } from "@/lib/auth";

/** Signed-in e-mail plus "Abmelden" / "Auf allen Geräten abmelden". */
export function UserMenu() {
  const { data: me } = useMe();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);

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
    <div className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        className="max-w-[12rem] truncate rounded-md border bg-card px-2 py-1 text-sm"
      >
        {me.email}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 z-30 mt-1 w-56 rounded-md border bg-card p-1 text-sm shadow"
        >
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
