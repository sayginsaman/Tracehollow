import type { Metadata } from "next";

import { CaseMembersView } from "@/components/team/CaseMembersView";

export const metadata: Metadata = { title: "Members" };

export default function CaseMembersViewPage() {
  return <CaseMembersView />;
}
