import { useQueryClient } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { buttonClass, inputClass, primaryClass } from "@/components/household/forms";
import { useMe } from "@/lib/auth";
import {
  ACCESS_TEXT,
  accessError,
  endUserSessions,
  inviteUser,
  patchUser,
  USERS_KEY,
  useUsers,
  type UserOut,
} from "@/lib/users";

const day = (iso: string) => new Date(iso).toLocaleDateString("de-DE");

function Row({
  user,
  lastOwner,
  onDone,
}: {
  user: UserOut;
  lastOwner: boolean;
  onDone: () => Promise<void>;
}) {
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const [email, setEmail] = useState("");

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true);
    try {
      await action();
      toast.success(success);
      setChanging(false);
      setEmail("");
      await onDone();
    } catch (error) {
      toast.error(accessError(error));
    } finally {
      setBusy(false);
    }
  }

  // The api answers 409 for the last active owner too; the buttons are disabled up front.
  const protectedOwner = lastOwner && user.role === "owner" && !user.disabled;
  const small =
    "min-h-11 rounded-md border px-3 py-1 text-sm hover:bg-secondary disabled:opacity-50";

  return (
    <li className="space-y-2 py-3">
      <div>
        <p className="break-all font-medium">
          {user.email}
          {user.is_self ? " (du)" : ""}
        </p>
        <p className="text-xs text-muted-foreground">
          {user.role === "owner" ? ACCESS_TEXT.owner : ACCESS_TEXT.member}
          {user.disabled ? ` · ${ACCESS_TEXT.disabled}` : ""}
          {" · "}
          {user.last_login_at
            ? `zuletzt angemeldet am ${day(user.last_login_at)}`
            : ACCESS_TEXT.never}
        </p>
      </div>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          className={small}
          disabled={busy || (user.role === "owner" && protectedOwner)}
          title={protectedOwner ? ACCESS_TEXT.lastOwner : undefined}
          onClick={() =>
            void run(
              () => patchUser(user.id, { role: user.role === "owner" ? "member" : "owner" }),
              ACCESS_TEXT.saved,
            )
          }
        >
          {user.role === "owner" ? ACCESS_TEXT.makeMember : ACCESS_TEXT.makeOwner}
        </button>
        {user.disabled ? (
          <button
            type="button"
            className={small}
            disabled={busy}
            onClick={() =>
              void run(() => patchUser(user.id, { disabled: false }), ACCESS_TEXT.saved)
            }
          >
            {ACCESS_TEXT.enable}
          </button>
        ) : (
          <button
            type="button"
            className={`${small} text-destructive`}
            disabled={busy || protectedOwner}
            title={protectedOwner ? ACCESS_TEXT.lastOwner : ACCESS_TEXT.disableHint}
            onClick={() => {
              if (window.confirm(`Zugang sperren? ${ACCESS_TEXT.disableHint}`))
                void run(() => patchUser(user.id, { disabled: true }), ACCESS_TEXT.saved);
            }}
          >
            {ACCESS_TEXT.disable}
          </button>
        )}
        <button
          type="button"
          className={small}
          disabled={busy}
          onClick={() => void run(() => endUserSessions(user.id), ACCESS_TEXT.sessionsEnded)}
        >
          {ACCESS_TEXT.endSessions}
        </button>
        <button type="button" className={small} onClick={() => setChanging((v) => !v)}>
          {ACCESS_TEXT.changeEmail}
        </button>
      </div>
      {changing && (
        <form
          className="flex flex-col gap-2 sm:flex-row"
          onSubmit={(e) => {
            e.preventDefault();
            void run(() => patchUser(user.id, { email }), ACCESS_TEXT.emailSent);
          }}
        >
          <input
            type="email"
            required
            aria-label={ACCESS_TEXT.newEmail}
            placeholder={ACCESS_TEXT.newEmail}
            autoComplete="off"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={inputClass}
          />
          <button type="submit" className={primaryClass} disabled={busy}>
            {ACCESS_TEXT.send}
          </button>
        </form>
      )}
    </li>
  );
}

/** "Zugänge" (#34): owners only; a member (or a failed `/auth/me`) sees nothing. */
export function AccessCard() {
  const me = useMe();
  const isOwner = me.data?.role === "owner";
  const users = useUsers(isOwner);
  const queryClient = useQueryClient();
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"owner" | "member">("member");
  const [busy, setBusy] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: USERS_KEY });

  if (!isOwner) return null;
  const activeOwners = (users.data ?? []).filter((u) => u.role === "owner" && !u.disabled).length;

  async function invite() {
    setBusy(true);
    try {
      await inviteUser(email, role);
      toast.success(ACCESS_TEXT.invited);
      setEmail("");
      await refresh();
    } catch (error) {
      toast.error(accessError(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="sheet space-y-3 p-4 sm:p-6" aria-labelledby="access-title">
      <h2 id="access-title" className="flex items-center gap-2 text-2xl">
        <KeyRound className="h-5 w-5 text-primary" /> {ACCESS_TEXT.title}
      </h2>
      <p className="text-sm text-muted-foreground">{ACCESS_TEXT.intro}</p>
      {users.isError && (
        <p role="alert" className="text-sm text-destructive">
          {ACCESS_TEXT.loadError}
        </p>
      )}
      {users.data && (
        <ul className="divide-y">
          {users.data.map((u) => (
            <Row key={u.id} user={u} lastOwner={activeOwners <= 1} onDone={refresh} />
          ))}
        </ul>
      )}
      <form
        className="flex flex-col gap-2 sm:flex-row"
        onSubmit={(e) => {
          e.preventDefault();
          void invite();
        }}
      >
        <input
          type="email"
          required
          aria-label={ACCESS_TEXT.inviteEmail}
          placeholder={ACCESS_TEXT.inviteEmail}
          autoComplete="off"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          className={inputClass}
        />
        <select
          aria-label="Rolle"
          value={role}
          onChange={(e) => setRole(e.target.value === "owner" ? "owner" : "member")}
          className="min-h-11 rounded-md border bg-card px-3 text-sm"
        >
          <option value="member">{ACCESS_TEXT.member}</option>
          <option value="owner">{ACCESS_TEXT.owner}</option>
        </select>
        <button type="submit" className={buttonClass} disabled={busy}>
          {ACCESS_TEXT.invite}
        </button>
      </form>
    </section>
  );
}
