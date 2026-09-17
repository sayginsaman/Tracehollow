import type { Metadata } from "next";

import { MonitorsView } from "@/components/monitors/MonitorsView";

export const metadata: Metadata = { title: "Monitors" };

export default async function MonitorsPage({ searchParams }: PageProps<"/cases/[caseId]/monitors">) {
  const { new: create } = await searchParams;
  return <MonitorsView startCreating={create === "1"} />;
}
