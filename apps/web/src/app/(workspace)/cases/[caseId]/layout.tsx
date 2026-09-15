import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { CaseProvider } from "@/components/cases/CaseContext";
import { CaseHeader } from "@/components/cases/CaseHeader";
import { fetchAuthenticated } from "@/lib/server-api";
import type { CaseDetail } from "@/lib/workspace-types";

export const dynamic = "force-dynamic";

export default async function CaseLayout({ children, params }: LayoutProps<"/cases/[caseId]">) {
  const { caseId } = await params;
  const result = await fetchAuthenticated<CaseDetail>(`/api/v1/cases/${encodeURIComponent(caseId)}`);
  if (result.kind === "error" && result.status === 401) redirect("/login");
  if (result.kind === "error" && (result.status === 404 || result.status === 422)) notFound();
  if (result.kind === "error" && result.status === 409) {
    return (
      <div className="space-y-3">
        <h1 className="text-2xl font-semibold">Case unavailable</h1>
        <p role="alert" className="text-sm text-muted">
          This case is scheduled for deletion or its deletion failed. Its records can no longer be opened. Check
          the deletion jobs on the <Link href="/cases" className="text-accent underline">cases page</Link>.
        </p>
      </div>
    );
  }
  if (result.kind !== "ok") {
    return (
      <p role="alert" className="text-sm text-bad">
        The case could not be loaded because the API did not respond. Try again shortly.
      </p>
    );
  }

  return (
    <CaseProvider initialCase={result.data}>
      <div className="space-y-6">
        <CaseHeader />
        {children}
      </div>
    </CaseProvider>
  );
}
