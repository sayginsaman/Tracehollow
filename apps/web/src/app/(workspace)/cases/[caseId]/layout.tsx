import { notFound, redirect } from "next/navigation";

import { CaseProvider } from "@/components/cases/CaseContext";
import { ArchivedCaseNotice } from "@/components/cases/ArchivedCaseNotice";
import { AppShell } from "@/components/shell/AppShell";
import { ButtonLink, EmptyState, PageHeader } from "@/components/ui";
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
      <AppShell>
        <div className="space-y-6">
          <PageHeader title="Case unavailable" />
          <EmptyState title="This case can no longer be opened" action={<ButtonLink href="/cases">Go to cases</ButtonLink>}>
            <p role="alert">
              It is scheduled for deletion or its deletion failed, so its records can no longer be opened. The deletion job
              and a retry action are listed on the cases page.
            </p>
          </EmptyState>
        </div>
      </AppShell>
    );
  }
  if (result.kind !== "ok") {
    return (
      <AppShell>
        <div className="space-y-6">
          <PageHeader title="Case not loaded" />
          <EmptyState title="The API did not respond" action={<ButtonLink href={`/cases/${encodeURIComponent(caseId)}`}>Try again</ButtonLink>}>
            <p role="alert">The case could not be loaded because the Tracehollow API did not respond. Check Environment status, then try again.</p>
          </EmptyState>
        </div>
      </AppShell>
    );
  }

  return (
    <CaseProvider initialCase={result.data}>
      <AppShell>
        <ArchivedCaseNotice />
        {children}
      </AppShell>
    </CaseProvider>
  );
}
