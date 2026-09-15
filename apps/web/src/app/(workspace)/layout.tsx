import { redirect } from "next/navigation";

import { WorkspaceShell } from "@/components/WorkspaceShell";
import { SessionProvider } from "@/lib/session-context";
import { fetchSession } from "@/lib/server-api";

export const dynamic = "force-dynamic";

export default async function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const session = await fetchSession();
  if (session.kind !== "ok") redirect("/login");

  return (
    <SessionProvider session={session.data}>
      <WorkspaceShell>{children}</WorkspaceShell>
    </SessionProvider>
  );
}
