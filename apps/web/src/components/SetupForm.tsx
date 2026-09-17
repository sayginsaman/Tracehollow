"use client";

import { useState, type FormEvent } from "react";

import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";

import { Button, Field, FormError, TextInput } from "./ui";

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
      <Field label="Setup token" htmlFor="setup-token" hint="Stored in secrets/bootstrap_token on the machine running Tracehollow.">
        <TextInput id="setup-token" name="setup-token" type="password" autoComplete="off" spellCheck={false} required maxLength={256} />
      </Field>
      <Field label="Administrator username" htmlFor="username" hint="3 to 64 letters, digits, dots, underscores or hyphens.">
        <TextInput id="username" name="username" autoComplete="username" required minLength={3} maxLength={64} />
      </Field>
      <Field label="Password" htmlFor="password" hint={`At least ${PASSWORD_MIN_LENGTH} characters. A long passphrase is recommended.`}>
        <TextInput
          id="password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={PASSWORD_MIN_LENGTH}
          maxLength={1024}
        />
      </Field>
      <Field label="Repeat password" htmlFor="password-confirmation">
        <TextInput id="password-confirmation" name="password-confirmation" type="password" autoComplete="new-password" required maxLength={1024} />
      </Field>
      <Button type="submit" variant="primary" className="w-full" disabled={pending} busy={pending}>
        {pending ? "Creating administrator…" : "Create administrator"}
      </Button>
    </form>
  );
}
