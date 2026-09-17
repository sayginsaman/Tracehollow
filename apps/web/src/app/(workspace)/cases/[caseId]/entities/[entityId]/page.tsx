import type { Metadata } from "next";

import { EntityDetailView } from "@/components/cases/EntityDetailView";

export const metadata: Metadata = { title: "Entity" };

export default async function EntityDetailPage({ params }: PageProps<"/cases/[caseId]/entities/[entityId]">) {
  const { entityId } = await params;
  return <EntityDetailView entityId={entityId} />;
}
