import type { Metadata } from "next";

import { NotificationsView } from "@/components/notifications/NotificationsView";

export const metadata: Metadata = { title: "Notifications" };

export default function NotificationsViewPage() {
  return <NotificationsView />;
}
