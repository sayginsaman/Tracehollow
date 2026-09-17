import type { Metadata } from "next";

import { AdminAuditView } from "@/components/admin/AdminAuditView";

export const metadata: Metadata = { title: "Audit log" };

export default function AdminAuditViewPage() {
  return <AdminAuditView />;
}
