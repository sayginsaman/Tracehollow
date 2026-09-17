import type { Metadata } from "next";

import { DestinationsView } from "@/components/admin/DestinationsView";

export const metadata: Metadata = { title: "Notification destinations" };

export default function DestinationsViewPage() {
  return <DestinationsView />;
}
