import type { Metadata } from "next";

import { CompareView } from "@/components/cases/CompareView";

export const metadata: Metadata = { title: "Compare" };

export default async function ComparePage({ searchParams }: PageProps<"/cases/[caseId]/compare">) {
  const { entity_id: entityIds } = await searchParams;
  const initialSelection = (Array.isArray(entityIds) ? entityIds : entityIds ? [entityIds] : []).filter((id) => /^[0-9a-f-]{36}$/i.test(id));
  return <CompareView initialSelection={initialSelection} />;
}
