import type { Metadata } from "next";

import { AccountsView } from "@/components/admin/AccountsView";

export const metadata: Metadata = { title: "Accounts" };

export default function AccountsViewPage() {
  return <AccountsView />;
}
