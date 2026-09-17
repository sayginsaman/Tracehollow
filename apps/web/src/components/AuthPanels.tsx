"use client";

import { useRouter } from "next/navigation";

import { LoginForm } from "./LoginForm";
import { SetupForm } from "./SetupForm";

export function LoginPanel() {
  const router = useRouter();
  return (
    <LoginForm
      onSuccess={() => {
        router.replace("/overview");
        router.refresh();
      }}
    />
  );
}

export function SetupPanel() {
  const router = useRouter();
  return (
    <SetupForm
      onSuccess={() => {
        router.replace("/login?setup=complete");
        router.refresh();
      }}
    />
  );
}
