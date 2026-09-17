import type { Metadata } from "next";

import { CaseOverview } from "@/components/cases/CaseOverview";

export const metadata: Metadata = { title: "Case overview" };

export default function CaseOverviewPage() {
  return <CaseOverview />;
}
