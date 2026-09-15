import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { LoginForm } from "./LoginForm";

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
  });
}

async function submit(username = "analyst", password = "correct horse battery") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Username"), username);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  it("posts credentials to the same-origin API and reports success", async () => {
    const session = { user: { id: "1", username: "analyst", is_admin: true }, csrf_token: "t" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(200, session));
    const onSuccess = vi.fn();
    render(<LoginForm onSuccess={onSuccess} />);

    await submit();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/login",
      expect.objectContaining({ method: "POST", credentials: "same-origin" }),
    );
    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body).toEqual({ username: "analyst", password: "correct horse battery" });
    expect(onSuccess).toHaveBeenCalledWith(session);
  });

  it("shows a clear error for invalid credentials and does not sign in", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse(401, { detail: "invalid_credentials" }));
    const onSuccess = vi.fn();
    render(<LoginForm onSuccess={onSuccess} />);

    await submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("Incorrect username or password.");
    expect(onSuccess).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("explains temporary lockouts with the retry delay", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse(429, { detail: "too_many_failed_attempts" }, { "retry-after": "45" }),
    );
    render(<LoginForm onSuccess={vi.fn()} />);

    await submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("Try again in 45 seconds.");
  });

  it("reports an unreachable API instead of failing silently", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new TypeError("fetch failed"));
    render(<LoginForm onSuccess={vi.fn()} />);

    await submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("could not be sent");
  });
});
