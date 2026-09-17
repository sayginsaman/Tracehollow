import type { Metadata } from "next";

import { EntityList } from "@/components/cases/EntityList";

export const metadata: Metadata = { title: "Entities" };

export default function EntitiesPage() {
  return <EntityList />;
}
