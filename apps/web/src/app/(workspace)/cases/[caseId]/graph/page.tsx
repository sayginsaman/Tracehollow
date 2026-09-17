import type { Metadata } from "next";

import { GraphView } from "@/components/cases/GraphView";

export const metadata: Metadata = { title: "Graph" };

export default function GraphPage() {
  return <GraphView />;
}
