import type { Metadata } from "next";

import { ImportsView } from "@/components/cases/ImportsView";

export const metadata: Metadata = { title: "Imports" };

export default function ImportsPage() {
  return <ImportsView />;
}
