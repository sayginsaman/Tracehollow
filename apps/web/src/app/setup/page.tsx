import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { SetupPanel } from "@/components/AuthPanels";
import { AuthShell } from "@/components/AuthShell";
import { DependencyNotice } from "@/components/DependencyNotice";
import { fetchReadiness, fetchSetupStatus } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "First-run setup" };

export default async function SetupPage() {
  const [setup, readiness] = await Promise.all([fetchSetupStatus(), fetchReadiness()]);
  if (setup.kind === "ok" && !setup.data.setup_required) redirect("/login");

  return (
    <AuthShell
      title="Create the administrator"
      description={
        <p>
          This one-time step creates the local administrator account. It requires the setup token
          generated on the machine that runs Tracehollow, so only someone with access to that machine
          can complete it.
        </p>
      }
    >
      <DependencyNotice readiness={readiness} />
      {setup.kind === "ok" ? (
        <SetupPanel />
      ) : (
        <p className="text-sm text-muted">Setup is unavailable until the API and database respond.</p>
      )}
    </AuthShell>
  );
}
