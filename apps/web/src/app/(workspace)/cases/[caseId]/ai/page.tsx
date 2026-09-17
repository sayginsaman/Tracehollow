import type { Metadata } from "next";

import { AiWorkspace } from "@/components/ai/AiWorkspace";

export const metadata: Metadata = { title: "AI" };

export default function CaseAiPage() {
  return <AiWorkspace />;
}
