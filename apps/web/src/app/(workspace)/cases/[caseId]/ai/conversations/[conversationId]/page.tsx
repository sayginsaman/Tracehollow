import type { Metadata } from "next";

import { ConversationView } from "@/components/ai/ConversationView";

export const metadata: Metadata = { title: "AI conversation" };

export default async function ConversationPage({ params }: PageProps<"/cases/[caseId]/ai/conversations/[conversationId]">) {
  const { conversationId } = await params;
  return <ConversationView conversationId={conversationId} />;
}
