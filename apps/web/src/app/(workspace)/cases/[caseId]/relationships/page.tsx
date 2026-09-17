import type { Metadata } from "next";

import { RelationshipsView } from "@/components/cases/RelationshipsView";

export const metadata: Metadata = { title: "Relationships" };

export default function RelationshipsPage() {
  return <RelationshipsView />;
}
