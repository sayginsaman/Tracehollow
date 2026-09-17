import type { Metadata } from "next";

import { EvidenceDetailView } from "@/components/cases/EvidenceDetailView";

export const metadata: Metadata = { title: "Evidence record" };

export default async function EvidenceDetailPage({ params }: PageProps<"/cases/[caseId]/evidence/[evidenceId]">) {
  const { evidenceId } = await params;
  return <EvidenceDetailView evidenceId={evidenceId} />;
}
