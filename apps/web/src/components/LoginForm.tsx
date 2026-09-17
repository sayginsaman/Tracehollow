"use client";

import { useState, type FormEvent } from "react";

import type { SessionInfo } from "@/lib/api-types";
import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";

import { Button, Field, FormError, TextInput } from "./ui";

export function LoginForm({ onSuccess }: { onSuccess: (session: SessionInfo) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setPending(true);
    setError(null);
    try {
      const session = await apiRequest<SessionInfo>("/api/v1/auth/login", {
        method: "POST",
        body: { username: String(form.get("username") ?? ""), password: String(form.get("password") ?? "") },
      });
      onSuccess(session);
    } catch (caught) {
      setError(describeError(caught));
      setPending(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <FormError message={error} />
      <Field label="Username" htmlFor="username">
        <TextInput id="username" name="username" autoComplete="username" required maxLength={128} autoFocus />
      </Field>
      <Field label="Password" htmlFor="password">
        <TextInput id="password" name="password" type="password" autoComplete="current-password" required maxLength={1024} />
      </Field>
      <Button type="submit" variant="primary" className="w-full" disabled={pending} busy={pending}>
        {pending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}
