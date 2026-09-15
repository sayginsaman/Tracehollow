import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { SetupForm, validateSetupInput } from "./SetupForm";

async function fill(values: { token?: string; username?: string; password?: string; confirm?: string }) {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Setup token"), values.token ?? "a".repeat(48));
  await user.type(screen.getByLabelText("Administrator username"), values.username ?? "Şule.Yılmaz");
  await user.type(screen.getByLabelText("Password"), values.password ?? "a long enough passphrase");
  await user.type(screen.getByLabelText("Repeat password"), values.confirm ?? values.password ?? "a long enough passphrase");
  await user.click(screen.getByRole("button", { name: "Create administrator" }));
}

describe("validateSetupInput", () => {
  it("requires matching passwords of the minimum length", () => {
    expect(validateSetupInput("short", "short")).toMatch(/at least 12/);
    expect(validateSetupInput("long enough value", "different value!")).toMatch(/do not match/);
    expect(validateSetupInput("long enough value", "long enough value")).toBeNull();
  });
});

describe("SetupForm", () => {
  it("does not call the API when passwords do not match", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<SetupForm onSuccess={vi.fn()} />);

    await fill({ password: "a long enough passphrase", confirm: "another long passphrase" });

    expect(await screen.findByRole("alert")).toHaveTextContent("The passwords do not match.");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits a Unicode username and trimmed token", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response(JSON.stringify({ id: "1", username: "Şule.Yılmaz" }), { status: 201 }));
    const onSuccess = vi.fn();
    render(<SetupForm onSuccess={onSuccess} />);

    await fill({ token: `  ${"b".repeat(48)}  ` });

    const body = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(body).toEqual({ setup_token: "b".repeat(48), username: "Şule.Yılmaz", password: "a long enough passphrase" });
    expect(onSuccess).toHaveBeenCalledOnce();
  });

  it("explains an invalid setup token", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ detail: "setup_token_invalid" }), { status: 403 }),
    );
    const onSuccess = vi.fn();
    render(<SetupForm onSuccess={onSuccess} />);

    await fill({});

    expect(await screen.findByRole("alert")).toHaveTextContent("secrets/bootstrap_token");
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
