import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { StatusDashboard } from "@/components/StatusDashboard";
import { fetchSession } from "@/lib/server-api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Environment status" };

export default async function StatusPage() {
  const session = await fetchSession();
  if (session.kind !== "ok") redirect("/login");

  return <StatusDashboard session={session.data} />;
}
