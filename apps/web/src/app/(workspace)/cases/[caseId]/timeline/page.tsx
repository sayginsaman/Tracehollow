import type { Metadata } from "next";

import { TimelineView } from "@/components/cases/TimelineView";

export const metadata: Metadata = { title: "Timeline" };

export default function TimelinePage() {
  return <TimelineView />;
}
