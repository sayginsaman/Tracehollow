import type { Metadata } from "next";

import { CaseList } from "@/components/cases/CaseList";

export const metadata: Metadata = { title: "Cases" };

export default async function CasesPage({ searchParams }: PageProps<"/cases">) {
  const { new: create, deletion } = await searchParams;
  return <CaseList startCreating={create === "1"} deletionRequested={deletion === "requested"} />;
}
