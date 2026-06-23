import type { ChatMessage } from "../../types";
import { AgentWorkLog } from "./AgentWorkLog";

interface PendingAssistantProps {
  message: ChatMessage;
}

export function PendingAssistant({ message }: PendingAssistantProps) {
  return <AgentWorkLog message={message} variant="live" />;
}
