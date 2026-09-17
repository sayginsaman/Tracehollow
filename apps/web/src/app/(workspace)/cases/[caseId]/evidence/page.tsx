import type { Metadata } from "next";

import { EvidenceList } from "@/components/cases/EvidenceList";

export const metadata: Metadata = { title: "Evidence" };

export default function EvidencePage() {
  return <EvidenceList />;
}
