import type { Metadata } from "next";

import { WorkspaceOverview } from "@/components/overview/WorkspaceOverview";

export const metadata: Metadata = { title: "Overview" };

export default function OverviewPage() {
  return <WorkspaceOverview />;
}
