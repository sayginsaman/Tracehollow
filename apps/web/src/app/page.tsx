import { redirect } from "next/navigation";

import { AuthShell } from "@/components/AuthShell";
import { DependencyNotice } from "@/components/DependencyNotice";
import { fetchReadiness, fetchSession, fetchSetupStatus } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const setup = await fetchSetupStatus();
  if (setup.kind === "ok") {
    if (setup.data.setup_required) redirect("/setup");
    const session = await fetchSession();
    redirect(session.kind === "ok" ? "/cases" : "/login");
  }

  const readiness = await fetchReadiness();
  return (
    <AuthShell
      title="Tracehollow is starting or unavailable"
      description="The setup state could not be determined because a required service did not respond."
    >
      <DependencyNotice readiness={readiness ?? null} />
      <p className="text-sm text-muted">
        Reload this page once the services report healthy. See the README troubleshooting section
        for help.
      </p>
    </AuthShell>
  );
}
