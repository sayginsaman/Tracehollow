import { EvidenceDetailView } from "@/components/cases/EvidenceDetailView";

export default async function EvidenceDetailPage({ params }: PageProps<"/cases/[caseId]/evidence/[evidenceId]">) {
  const { evidenceId } = await params;
  return <EvidenceDetailView evidenceId={evidenceId} />;
}
