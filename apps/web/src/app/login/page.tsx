import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LoginPanel } from "@/components/AuthPanels";
import { AuthShell } from "@/components/AuthShell";
import { DependencyNotice } from "@/components/DependencyNotice";
import { fetchReadiness, fetchSession, fetchSetupStatus } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const [setup, readiness] = await Promise.all([fetchSetupStatus(), fetchReadiness()]);
  if (setup.kind === "ok" && setup.data.setup_required) redirect("/setup");
  if (setup.kind === "ok" && (await fetchSession()).kind === "ok") redirect("/status");

  const { setup: setupFlag } = await searchParams;
  return (
    <AuthShell title="Sign in" description="Sign in with the local administrator account.">
      {setupFlag === "complete" ? (
        <p role="status" className="mb-4 rounded-md border border-ok/30 bg-ok-bg px-3 py-2 text-sm text-ok">
          Administrator created. Sign in to continue.
        </p>
      ) : null}
      <DependencyNotice readiness={readiness} />
      <LoginPanel />
    </AuthShell>
  );
}
