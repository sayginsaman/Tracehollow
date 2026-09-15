"use client";

import { useState, type FormEvent } from "react";

import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";

import { FormError, FormField, SubmitButton } from "./FormField";

export const PASSWORD_MIN_LENGTH = 12;

export function validateSetupInput(password: string, confirmation: string): string | null {
  if (password.length < PASSWORD_MIN_LENGTH) {
    return `Password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
  }
  if (password !== confirmation) return "The passwords do not match.";
  return null;
}

export function SetupForm({ onSuccess }: { onSuccess: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const password = String(form.get("password") ?? "");
    const problem = validateSetupInput(password, String(form.get("password-confirmation") ?? ""));
    if (problem) {
      setError(problem);
      return;
    }
    setPending(true);
    setError(null);
    try {
      await apiRequest("/api/v1/setup/admin", {
        method: "POST",
        body: {
          setup_token: String(form.get("setup-token") ?? "").trim(),
          username: String(form.get("username") ?? ""),
          password,
        },
      });
      onSuccess();
    } catch (caught) {
      setError(describeError(caught));
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormError message={error} />
      <FormField
        id="setup-token"
        label="Setup token"
        type="password"
        autoComplete="off"
        spellCheck={false}
        required
        maxLength={256}
        hint="Stored in secrets/bootstrap_token on the machine running Tracehollow."
      />
      <FormField
        id="username"
        label="Administrator username"
        autoComplete="username"
        required
        minLength={3}
        maxLength={64}
        hint="3–64 letters, digits, dots, underscores or hyphens."
      />
      <FormField
        id="password"
        label="Password"
        type="password"
        autoComplete="new-password"
        required
        minLength={PASSWORD_MIN_LENGTH}
        maxLength={1024}
        hint={`At least ${PASSWORD_MIN_LENGTH} characters. A long passphrase is recommended.`}
      />
      <FormField
        id="password-confirmation"
        label="Repeat password"
        type="password"
        autoComplete="new-password"
        required
        maxLength={1024}
      />
      <SubmitButton pending={pending}>{pending ? "Creating administrator…" : "Create administrator"}</SubmitButton>
    </form>
  );
}
