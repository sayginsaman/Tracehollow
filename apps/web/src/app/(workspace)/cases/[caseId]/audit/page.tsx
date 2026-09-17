import type { Metadata } from "next";

import { CaseAuditView } from "@/components/audit/CaseAuditView";

export const metadata: Metadata = { title: "Case audit log" };

export default function CaseAuditViewPage() {
  return <CaseAuditView />;
}
