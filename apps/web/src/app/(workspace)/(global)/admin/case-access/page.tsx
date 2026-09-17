import type { Metadata } from "next";

import { CaseAccessView } from "@/components/admin/CaseAccessView";

export const metadata: Metadata = { title: "Case access" };

export default async function CaseAccessPage({ searchParams }: PageProps<"/admin/case-access">) {
  const { without_analyst: withoutAnalyst } = await searchParams;
  return <CaseAccessView withoutAnalyst={withoutAnalyst === "1"} />;
}
