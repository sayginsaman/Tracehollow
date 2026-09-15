"use client";

import { useState, type FormEvent } from "react";

import type { SessionInfo } from "@/lib/api-types";
import { apiRequest } from "@/lib/client-api";
import { describeError } from "@/lib/messages";

import { FormError, FormField, SubmitButton } from "./FormField";

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
    <form onSubmit={handleSubmit} className="space-y-4" noValidate={false}>
      <FormError message={error} />
      <FormField id="username" label="Username" autoComplete="username" required maxLength={128} />
      <FormField
        id="password"
        label="Password"
        type="password"
        autoComplete="current-password"
        required
        maxLength={1024}
      />
      <SubmitButton pending={pending}>{pending ? "Signing in…" : "Sign in"}</SubmitButton>
    </form>
  );
}
