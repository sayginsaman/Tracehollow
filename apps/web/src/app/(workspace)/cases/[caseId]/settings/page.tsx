import type { Metadata } from "next";

import { CaseSettings } from "@/components/cases/CaseSettings";

export const metadata: Metadata = { title: "Case settings" };

export default function CaseSettingsPage() {
  return <CaseSettings />;
}
