"use client";

import { AUDIT_LIMITS, AuditTable } from "../audit/AuditTable";
import { PageHeader, Panel } from "../ui";
import { AdminGate } from "./AdminGate";

export function AdminAuditView() {
  return (
    <AdminGate permission="audit.system.read">
      <div className="space-y-6">
        <PageHeader
          title="Audit log"
          description="Account, access, credential and destination changes, denied requests, and case events across the installation. Entries name objects by identifier; they do not include case content."
        />
        <p className="max-w-[72ch] text-sm text-muted">{AUDIT_LIMITS}</p>
        <Panel title="Events" flush>
          <AuditTable path="/api/v1/admin/audit-events" showCase />
        </Panel>
      </div>
    </AdminGate>
  );
}
