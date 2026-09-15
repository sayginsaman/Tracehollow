import { ConversationView } from "@/components/ai/ConversationView";

export default async function ConversationPage({ params }: PageProps<"/cases/[caseId]/ai/conversations/[conversationId]">) {
  const { conversationId } = await params;
  return <ConversationView conversationId={conversationId} />;
}
