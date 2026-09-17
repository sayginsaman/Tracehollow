import { redirect } from "next/navigation";

import { SessionProvider } from "@/lib/session-context";
import { fetchSession } from "@/lib/server-api";

export const dynamic = "force-dynamic";

/** Every workspace page requires a session. The shell is rendered by the nested layouts. */
export default async function WorkspaceLayout({ children }: { children: React.ReactNode }) {
  const session = await fetchSession();
  if (session.kind !== "ok") redirect("/login");

  return <SessionProvider session={session.data}>{children}</SessionProvider>;
}
