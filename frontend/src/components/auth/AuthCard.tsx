import type { ReactNode } from "react";

/** Centered card for the login pages, in the app shell's style. */
export function AuthCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-5">
      <div className="sheet w-full max-w-md p-8">
        <div className="flex items-baseline gap-1">
          <span className="font-display text-2xl font-semibold">belegbot</span>
          <span className="stamp text-primary">ESt</span>
        </div>
        <h1 className="mt-6 text-2xl">{title}</h1>
        <div className="mt-4 space-y-4 text-sm">{children}</div>
      </div>
    </div>
  );
}

export const primaryButton =
  "inline-flex w-full items-center justify-center rounded-md bg-primary px-4 py-2 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-50";

export const secondaryButton =
  "inline-flex w-full items-center justify-center rounded-md border bg-card px-4 py-2 text-sm font-medium transition-colors hover:bg-secondary disabled:cursor-not-allowed disabled:opacity-50";
