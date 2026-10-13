import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AccessCard } from "@/components/household/AccessCard";
import { ACCESS_TEXT, type UserOut } from "@/lib/users";
import { json, ME, mockFetch } from "@/test/fetch-mock";

afterEach(() => vi.unstubAllGlobals());

const owner: UserOut = {
  id: "u1",
  email: "owner@example.com",
  role: "owner",
  disabled: false,
  last_login_at: "2026-10-01T10:00:00Z",
  person_id: null,
  is_self: true,
};
const member: UserOut = {
  ...owner,
  id: "u2",
  email: "member@example.com",
  role: "member",
  is_self: false,
};

function renderCard() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <AccessCard />
    </QueryClientProvider>,
  );
}

describe("AccessCard (#34)", () => {
  it("shows nothing to a member and never calls /users", async () => {
    const { calls } = mockFetch((c) =>
      c.url === "/api/auth/me" ? json(200, { ...ME, role: "member" }) : json(403, {}),
    );
    renderCard();
    await waitFor(() => expect(calls.some((c) => c.url === "/api/auth/me")).toBe(true));
    expect(screen.queryByText(ACCESS_TEXT.title)).toBeNull();
    expect(calls.some((c) => c.url === "/api/users")).toBe(false);
  });

  it("lists users, invites, and disables the last owner's buttons", async () => {
    const users = [owner, member];
    const { calls } = mockFetch((c) => {
      if (c.url === "/api/auth/me") return json(200, ME);
      if (c.url === "/api/users" && c.method === "POST") {
        users.push({ ...member, id: "u3", email: "new@example.com" });
        return json(201, users[2]);
      }
      if (c.url === "/api/users") return json(200, users);
      return json(404, { detail: "not_found" });
    });
    renderCard();
    expect(await screen.findByText("owner@example.com (du)")).toBeTruthy();
    const disable = screen.getAllByRole("button", { name: ACCESS_TEXT.disable });
    expect((disable[0] as HTMLButtonElement).disabled).toBe(true); // the last active owner
    expect((disable[1] as HTMLButtonElement).disabled).toBe(false);

    fireEvent.change(screen.getByLabelText(ACCESS_TEXT.inviteEmail), {
      target: { value: "new@example.com" },
    });
    fireEvent.click(screen.getByRole("button", { name: ACCESS_TEXT.invite }));
    expect(await screen.findByText("new@example.com")).toBeTruthy();
    const post = calls.find((c) => c.method === "POST");
    expect(post?.headers.get("X-Requested-With")).toBe("belegbot");
    expect(post?.body).toEqual({ email: "new@example.com", role: "member" });
  });

  it("asks before disabling and sends disabled: true", async () => {
    vi.stubGlobal(
      "confirm",
      vi.fn(() => true),
    );
    const { calls } = mockFetch((c) => {
      if (c.url === "/api/auth/me") return json(200, ME);
      if (c.url === "/api/users/u2" && c.method === "PATCH") return json(200, member);
      if (c.url === "/api/users") return json(200, [owner, member]);
      return json(404, { detail: "not_found" });
    });
    renderCard();
    await screen.findByText("member@example.com");
    fireEvent.click(screen.getAllByRole("button", { name: ACCESS_TEXT.disable })[1]!);
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ disabled: true }),
    );
  });
});
