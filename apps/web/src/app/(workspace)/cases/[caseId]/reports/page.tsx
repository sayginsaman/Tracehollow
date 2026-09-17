import type { Metadata } from "next";

import { ReportBuilder } from "@/components/cases/ReportBuilder";

export const metadata: Metadata = { title: "Reports" };

export default function ReportsPage() {
  return <ReportBuilder />;
}
