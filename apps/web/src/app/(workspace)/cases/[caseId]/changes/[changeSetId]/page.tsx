import type { Metadata } from "next";

import { ChangeSetView } from "@/components/monitors/ChangeSetView";

export const metadata: Metadata = { title: "Changes" };

export default async function ChangeSetPage({ params }: PageProps<"/cases/[caseId]/changes/[changeSetId]">) {
  const { changeSetId } = await params;
  return <ChangeSetView changeSetId={changeSetId} />;
}
