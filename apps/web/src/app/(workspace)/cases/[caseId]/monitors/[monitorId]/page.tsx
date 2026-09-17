import type { Metadata } from "next";

import { MonitorDetailView } from "@/components/monitors/MonitorDetailView";

export const metadata: Metadata = { title: "Monitor" };

export default async function MonitorDetailPage({ params }: PageProps<"/cases/[caseId]/monitors/[monitorId]">) {
  const { monitorId } = await params;
  return <MonitorDetailView monitorId={monitorId} />;
}
