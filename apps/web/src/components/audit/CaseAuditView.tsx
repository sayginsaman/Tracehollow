"use client";

import { useCase } from "../cases/CaseContext";
import { Notice, PageHeader, Panel } from "../ui";
import { AUDIT_LIMITS, AuditTable } from "./AuditTable";

export function CaseAuditView() {
  const { apiBase, can } = useCase();
  return (
    <div className="space-y-6">
      <PageHeader title="Audit log" description="Who did what in this case: membership, collection, imports, exports, deletions, retention and denied attempts." />
      {can("audit.case.read") ? (
        <>
          <p className="max-w-[72ch] text-sm text-muted">{AUDIT_LIMITS}</p>
          <Panel title="Events" flush>
            <AuditTable path={`${apiBase}/audit-events`} />
          </Panel>
        </>
      ) : (
        <Notice tone="neutral">The audit log of a case is available to its analysts.</Notice>
      )}
    </div>
  );
}
