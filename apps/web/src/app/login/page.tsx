import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { LoginPanel } from "@/components/AuthPanels";
import { AuthShell } from "@/components/AuthShell";
import { DependencyNotice } from "@/components/DependencyNotice";
import { Notice } from "@/components/ui";
import { fetchReadiness, fetchSession, fetchSetupStatus } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const [setup, readiness] = await Promise.all([fetchSetupStatus(), fetchReadiness()]);
  if (setup.kind === "ok" && setup.data.setup_required) redirect("/setup");
  if (setup.kind === "ok" && (await fetchSession()).kind === "ok") redirect("/overview");

  const { setup: setupFlag } = await searchParams;
  return (
    <AuthShell title="Sign in" description="Sign in with the local administrator account.">
      {setupFlag === "complete" ? (
        <Notice tone="ok" live className="mb-4">
          Administrator created. Sign in to continue.
        </Notice>
      ) : null}
      <DependencyNotice readiness={readiness} />
      <LoginPanel />
    </AuthShell>
  );
}
