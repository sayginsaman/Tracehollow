import { RunDetailView } from "@/components/cases/RunDetailView";

export default async function RunDetailPage({ params }: PageProps<"/cases/[caseId]/runs/[runId]">) {
  const { runId } = await params;
  return <RunDetailView runId={runId} />;
}
