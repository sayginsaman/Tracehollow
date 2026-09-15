import type { Metadata } from "next";

import { CaseList } from "@/components/cases/CaseList";

export const metadata: Metadata = { title: "Cases" };

export default function CasesPage() {
  return <CaseList />;
}
